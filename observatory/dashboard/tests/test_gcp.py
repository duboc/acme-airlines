from datetime import UTC, date, datetime
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from google.api_core.exceptions import Forbidden, NotFound

from dashboard import app as app_module
from dashboard import service
from dashboard.app import create_app, select_source
from dashboard.gcp import GcpDataSource
from dashboard.source import FakeDataSource, SourceEmpty, SourceUnavailable

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def make(bq=None, mon=None, log=None, clock=None):
    ticks = clock or (lambda: 0.0)
    return GcpDataSource(
        "proj", bq or MagicMock(), mon or MagicMock(), log or MagicMock(),
        now=lambda: NOW, monotonic=ticks,
    )  # fmt: skip


def bq_with(table_ids, rows):
    bq = MagicMock()
    bq.list_tables.return_value = [NS(table_id=t) for t in table_ids]
    bq.query.return_value.result.return_value = rows
    return bq


def test_billing_table_missing_is_empty_with_message():
    src = make(bq_with(["something_else"], []))
    with pytest.raises(SourceEmpty, match="not enabled yet"):
        src.billing_rows(date(2026, 10, 1))
    bq = MagicMock()
    bq.list_tables.side_effect = NotFound("no dataset")
    with pytest.raises(SourceEmpty):
        make(bq).billing_rows(date(2026, 10, 1))


def test_billing_query_is_parameterised_and_maps_rows():
    bq = bq_with(
        ["gcp_billing_export_v1_AB_CD"],
        [{"usage_date": date(2026, 10, 9), "service": "Vertex AI", "cost_usd": 4.5}],
    )
    rows = make(bq).billing_rows(date(2026, 10, 1))
    assert rows == [{"usage_date": "2026-10-09", "service": "Vertex AI", "cost_usd": 4.5}]
    sql = bq.query.call_args.args[0]
    assert "@start" in sql and "2026-10-01" not in sql
    assert "proj.billing_export.gcp_billing_export_v1_AB_CD" in sql
    params = bq.query.call_args.kwargs["job_config"].query_parameters
    assert [(p.name, p.value) for p in params] == [("start", date(2026, 10, 1))]


def test_billing_stale_is_lagging_via_service():
    class Stale(FakeDataSource):
        def billing_rows(self, start):
            return [{"usage_date": "2026-10-01", "service": "Cloud Run", "cost_usd": 1.0}]

    assert service.spend(Stale(), 30).state == "lagging"


def test_service_maps_source_empty_to_empty_state():
    class Off(FakeDataSource):
        def billing_rows(self, start):
            raise SourceEmpty("billing export not enabled yet")

    for resp in (service.spend(Off(), 30), service.budget(Off(), 1000)):
        assert resp.state == "empty" and "not enabled" in resp.message


def test_snapshot_queries_are_partition_pruned_and_jsonable():
    ts = datetime(2026, 10, 10, 11, 55, tzinfo=UTC)
    bq = bq_with([], [{"ts": ts, "open_prs": [{"number": 1, "created_at": ts}]}])
    rows = make(bq).github_snapshots()
    assert rows == [
        {"ts": ts.isoformat(), "open_prs": [{"number": 1, "created_at": ts.isoformat()}]}
    ]
    sql = bq.query.call_args.args[0]
    assert "proj.factory_ops.github_snapshots" in sql and "DATE(ts) >= @day" in sql
    make(bq).agent_snapshots()
    assert "proj.factory_ops.agent_snapshots" in bq.query.call_args.args[0]


def test_missing_snapshot_table_is_empty_list():
    bq = MagicMock()
    bq.query.side_effect = NotFound("table")
    assert make(bq).agent_snapshots() == []


def test_bigquery_permission_error_is_unavailable():
    bq = MagicMock()
    bq.query.side_effect = Forbidden("nope")
    with pytest.raises(SourceUnavailable):
        make(bq).github_snapshots()


def series(model, value):
    return NS(
        resource=NS(labels={"model_user_id": model}), points=[NS(value=NS(int64_value=value))]
    )


def test_monitoring_merges_tokens_and_requests_per_model():
    mon = MagicMock()
    mon.list_time_series.side_effect = [
        [series("claude-opus-5-5", 100), series("gemini-3-flash", 7)],
        [series("claude-opus-5-5", 3)],
    ]
    rows = make(mon=mon).monitoring_rows(24)
    assert {r["model_id"]: (r["tokens"], r["requests"]) for r in rows} == {
        "claude-opus-5-5": (100, 3),
        "gemini-3-flash": (7, 0),
    }
    filters = [c.kwargs["request"]["filter"] for c in mon.list_time_series.call_args_list]
    assert "online_serving/token_count" in filters[0]
    assert "online_serving/model_invocation_count" in filters[1]


def test_monitoring_error_is_unavailable_not_500():
    mon = MagicMock()
    mon.list_time_series.side_effect = RuntimeError("boom with secrets")
    src = make(mon=mon)
    with pytest.raises(SourceUnavailable) as e:
        src.monitoring_rows(24)
    assert "secrets" not in str(e.value)
    client = TestClient(create_app(src, budget_usd=1000))
    r = client.get("/api/models")
    assert r.status_code == 200 and r.json()["state"] == "unavailable"


def test_guard_logs_filter_and_parse_through_service():
    log = MagicMock()
    log.list_entries.return_value = [
        NS(payload="cost=412.37 budget=1000 -> agents suspended", timestamp=NOW),
        NS(payload={"json": "ignored"}, timestamp=NOW),
    ]
    src = make(log=log)
    rows = src.guard_logs(20)
    assert rows == [{"ts": NOW.isoformat(), "text": "cost=412.37 budget=1000 -> agents suspended"}]
    flt = log.list_entries.call_args.kwargs["filter_"]
    assert (
        'resource.labels.service_name="acme-budget-guard"' in flt and 'textPayload:"cost="' in flt
    )
    assert log.list_entries.call_args.kwargs["max_results"] == 20

    class Guard(FakeDataSource):
        def guard_logs(self, limit):
            return rows

    action = service.budget(Guard(), 1000).guard_actions[0]
    assert (action.cost_usd, action.budget_usd, action.action) == (
        412.37,
        1000.0,
        "agents suspended",
    )


def test_cache_bounds_queries_for_60s():
    bq = bq_with([], [{"ts": NOW, "name": "a"}])
    t = [0.0]
    src = make(bq, clock=lambda: t[0])
    src.agent_snapshots()
    src.agent_snapshots()
    assert bq.query.call_count == 1
    t[0] = 61
    src.agent_snapshots()
    assert bq.query.call_count == 2


def test_source_selection(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(app_module.GcpDataSource, "from_env", classmethod(lambda cls: sentinel))
    assert isinstance(select_source({"OBSERVATORY_DATA": "fake"}), FakeDataSource)
    assert select_source({}) is sentinel
    assert select_source({"OBSERVATORY_DATA": "real"}) is sentinel


def test_no_write_apis_used():
    bq = bq_with(["gcp_billing_export_v1_X"], [])
    src = make(bq)
    src.billing_rows(date(2026, 10, 1))
    src.agent_snapshots()
    called = {c[0] for c in bq.method_calls}
    assert called <= {"list_tables", "query"}


def test_collector_runs_map_log_lines_to_status():
    log = MagicMock()
    log.list_entries.return_value = [
        NS(timestamp=NOW, payload="Container called exit(2)."),
        NS(timestamp=NOW, payload="Container called exit(0)."),
        NS(timestamp=NOW, payload="wrote 12 rows to agent_snapshots"),
    ]
    rows = make(log=log).collector_runs(3)
    assert [r["status"] for r in rows] == ["failed", "succeeded", "succeeded"]
    assert "acme-observatory-collector" in log.list_entries.call_args.kwargs["filter_"]


def test_collector_runs_error_is_unavailable():
    log = MagicMock()
    log.list_entries.side_effect = Forbidden("no")
    with pytest.raises(SourceUnavailable):
        make(log=log).collector_runs(1)
