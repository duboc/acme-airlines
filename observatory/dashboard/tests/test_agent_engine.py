from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from google.api_core.exceptions import Forbidden
from google.cloud import monitoring_v3

from dashboard.app import create_app
from dashboard.gcp import GcpDataSource
from dashboard.source import FakeDataSource, TraceNotGranted

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def client(source):
    return TestClient(create_app(source, budget_usd=1000.0))


def test_endpoint_returns_metrics_and_traces_per_engine():
    body = client(FakeDataSource()).get("/api/agent-engine").json()
    assert body["state"] == "ok" and body["hours"] == 1
    c, s = body["engines"]
    assert (c["name"], c["requests"], c["errors"]) == ("acme-customer-agent", 200, 10)
    assert c["error_rate"] == pytest.approx(0.05)  # 10 / 200
    assert (c["latency_p50_ms"], c["latency_p95_ms"]) == (850.0, 2400.0)
    assert s["error_rate"] == 0 and c["trace_access"] == "granted"
    t = c["traces"][0]
    assert t["console_url"] == (
        "https://console.cloud.google.com/traces/list?project=acme-airlines-factory"
        "&tid=4bf92f3577b34da6a3ce929d0e0e4736"
    )
    assert set(t) == {"trace_id", "name", "start_time", "duration_ms", "status", "console_url"}


class Denied(FakeDataSource):
    def agent_engine_traces(self, name, limit):
        raise TraceNotGranted("no")


def test_trace_403_degrades_but_keeps_metrics():
    body = client(Denied()).get("/api/agent-engine").json()
    e = body["engines"][0]
    assert body["state"] == "ok"
    assert e["traces"] is None and e["trace_access"] == "not_granted"
    assert e["requests"] == 200


def test_no_engines_is_empty_not_error():
    r = client(FakeDataSource("empty")).get("/api/agent-engine")
    assert r.status_code == 200
    assert r.json()["state"] == "empty" and r.json()["engines"] == []


def test_unavailable_scenario():
    assert client(FakeDataSource("unavailable")).get("/api/agent-engine").json()["state"] == (
        "unavailable"
    )


def test_hours_validation():
    assert client(FakeDataSource()).get("/api/agent-engine?hours=0").status_code == 422


def ts(points, **labels):
    return NS(
        metric=NS(labels=labels),
        points=[NS(value=NS(int64_value=p, double_value=float(p))) for p in points],
    )


def gcp(mon=None, trace=None):
    return GcpDataSource(
        "proj", MagicMock(), mon or MagicMock(), MagicMock(), now=lambda: NOW,
        engines={"acme-customer-agent": "111"}, trace_client=trace,
    )  # fmt: skip


def test_gcp_metrics_computes_error_rate_and_percentiles():
    A = monitoring_v3.Aggregation.Aligner
    mon = MagicMock()

    def lts(request):
        agg = request["aggregation"]
        assert 'reasoning_engine_id="111"' in request["filter"]
        if "request_count" in request["filter"]:
            return [ts([180], response_code="200"), ts([15], response_code="429"),
                    ts([5], response_code="500")]  # fmt: skip
        return [ts([850 if agg.per_series_aligner == A.ALIGN_PERCENTILE_50 else 2400])]

    mon.list_time_series.side_effect = lts
    m = gcp(mon).agent_engine_metrics("111", 1)
    assert m == {"requests": 200, "errors": 20, "p50_ms": 850.0, "p95_ms": 2400.0}


def test_gcp_traces_map_root_spans_and_hide_labels():
    start = NOW - timedelta(minutes=5)
    span = NS(name="invoke_agent", start_time=start, end_time=start + timedelta(seconds=2),
              labels={"user.email": "x@y.z"})  # fmt: skip
    trace = MagicMock()
    trace.list_traces.return_value = [NS(trace_id="abc", spans=[span])]
    rows = gcp(trace=trace).agent_engine_traces("acme-customer-agent", 10)
    assert rows[0]["duration_ms"] == 2000.0 and rows[0]["status"] == "ok"
    assert "x@y.z" not in str(rows)
    assert (
        'service.name:"acme-customer-agent"'
        in trace.list_traces.call_args.kwargs["request"]["filter"]
    )


def test_gcp_traces_403_raises_not_granted():
    trace = MagicMock()
    trace.list_traces.side_effect = Forbidden("nope")
    with pytest.raises(TraceNotGranted):
        gcp(trace=trace).agent_engine_traces("acme-customer-agent", 10)


def test_gcp_no_config_means_no_engines():
    assert GcpDataSource("p", MagicMock(), MagicMock(), MagicMock()).agent_engines() == []
