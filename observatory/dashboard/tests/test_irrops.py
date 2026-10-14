from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from conftest import make_client
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.gcp import GcpDataSource
from dashboard.source import FakeDataSource, SourceEmpty

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def get(scenario="ok", hours=24):
    r = make_client(scenario).get(f"/api/irrops?hours={hours}")
    assert r.status_code == 200
    return r.json()


def test_fake_data_shows_every_metric_group():
    d = get()
    assert d["state"] == "ok" and d["message"] is None and d["hours"] == 24
    assert {"rule": "not_acknowledged", "outcome": "reject", "count": 4} in d["guardrail"]
    assert (d["offers_proposed"], d["offers_accepted"], d["offers_declined"]) == (7, 4, 2)
    assert d["template_fallbacks"] == 3 and d["template_fallback_rate"] == 0.25
    assert d["needs_human"] == 1
    assert (d["notification_p50_s"], d["notification_p95_s"]) == (8.0, 41.5)
    assert d["errors_5xx"] == 0 and d["errors_5xx_per_hour"] == 0
    assert d["engine_rebuilds"] == 2
    assert (d["engine_latency_p50_ms"], d["engine_latency_p95_ms"]) == (150.0, 400.0)
    assert d["demo_health"] == "healthy"


def test_empty_is_pending_with_null_values_not_zeros():
    d = get("empty")
    assert d["state"] == "empty" and d["message"]
    assert d["offers_proposed"] is None and d["errors_5xx"] is None and d["guardrail"] == []
    assert d["demo_health"] == "unknown"


def test_unavailable_and_lagging_scenarios():
    assert get("unavailable")["state"] == "unavailable"
    d = get("lagging")
    assert d["state"] == "lagging" and "hosted demo may be down" in d["message"]
    assert d["demo_health"] == "down" and d["offers_proposed"] == 7  # data stays visible


def test_5xx_marks_the_demo_degraded():
    class Errors(FakeDataSource):
        def irrops_metrics(self, hours):
            return {**super().irrops_metrics(hours), "errors_5xx": 6}

    d = TestClient(create_app(Errors())).get("/api/irrops?hours=3").json()
    assert d["demo_health"] == "degraded" and d["errors_5xx_per_hour"] == 2.0


def test_missing_metric_series_stay_null():
    class Partial(FakeDataSource):
        def irrops_metrics(self, hours):
            return {"last_point_at": "2026-10-10T12:00:00Z", "offers": {"proposed": 1}}

    d = TestClient(create_app(Partial())).get("/api/irrops").json()
    assert d["offers_proposed"] == 1 and d["offers_accepted"] is None
    assert d["needs_human"] is None and d["template_fallback_rate"] is None


def test_hours_is_validated():
    assert make_client().get("/api/irrops?hours=0").status_code == 422
    assert make_client().post("/api/irrops").status_code == 405


def series(labels, **value):
    point = MagicMock()
    point.value.int64_value = value.get("n", 0)
    point.value.double_value = value.get("d", 0.0)
    point.value.distribution_value.count = value.get("n", 0)
    point.interval.end_time = NOW
    ts = MagicMock()
    ts.metric.labels = labels
    ts.points = [point]
    return ts


def gcp(responses):
    mon = MagicMock()
    mon.list_time_series.side_effect = lambda request: responses(request["filter"])
    return GcpDataSource("proj", MagicMock(), mon, MagicMock(), now=lambda: NOW)


def test_gcp_reads_the_documented_metric_names():
    def responses(flt):
        if "starts_with" in flt:
            return [series({}, n=1)]
        if "acme.guardrail.decisions" in flt:
            return [series({"rule": "margin_negative", "outcome": "reject"}, n=2)]
        if "acme.offers" in flt:
            return [series({"status": "proposed"}, n=5), series({"status": "accepted"}, n=3)]
        if "acme.notification.latency" in flt:
            return [series({}, n=9, d=12.5)]
        if "acme.needs_human" in flt:
            return [series({}, n=1)]
        return []  # no template fallbacks / 5xx series yet

    raw = gcp(responses).irrops_metrics(24)
    assert raw["guardrail"] == [{"rule": "margin_negative", "outcome": "reject", "count": 2}]
    assert raw["offers"] == {"proposed": 5, "accepted": 3}
    assert raw["notification"] == {"count": 9, "p50_s": 12.5, "p95_s": 12.5}
    assert raw["needs_human"] == 1 and raw["template_fallbacks"] is None
    assert raw["errors_5xx"] is None and raw["alive"] is True


def test_gcp_without_any_series_is_empty_and_without_recent_points_is_not_alive():
    with pytest.raises(SourceEmpty, match="No IRROPS metrics yet"):
        gcp(lambda flt: []).irrops_metrics(24)

    def old(flt):
        return [] if "starts_with" in flt else ([series({}, n=2)] if "needs_human" in flt else [])

    raw = gcp(old).irrops_metrics(24)
    assert raw["alive"] is False and raw["last_point_at"] is None
