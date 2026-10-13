"""#221 live-proof tooling, offline: the unary probe report and the engine-log fetch."""

import json
from datetime import UTC, datetime, timedelta

from tests.live.agent_engine_proof import (
    CloudLogging,
    Span,
    Trace,
    parse_ts,
    probe_report,
    write_engine_logs,
)
from tests.live.test_cloud_trace_retry import Response, Session

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def op_trace(tid, at, op="journeys_list"):
    start = (T0 + timedelta(seconds=at)).isoformat().replace("+00:00", "Z")
    return Trace(tid, (Span("acme.engine.op", {"acme.op": op}, "s", "", start, start),))


def call(t0, t1, kind="back_to_back"):
    return {"kind": kind, "t0": T0 + timedelta(seconds=t0), "t1": T0 + timedelta(seconds=t1)}


def test_parse_ts_handles_nanoseconds_and_z():
    assert parse_ts("2026-01-01T12:00:00.123456789Z") == T0 + timedelta(microseconds=123456)
    assert parse_ts("2026-01-01T12:00:00Z") == T0


def test_probe_matches_each_call_to_one_trace_and_lists_the_lost():
    calls = [call(0, 1), call(10, 11), call(20, 21), call(60, 61, "spaced")]
    traces = [op_trace("a", 0.5), op_trace("b", 20.2), op_trace("c", 60.3)]
    rep = probe_report(calls, traces)
    assert (rep["sent"], rep["found"]) == (4, 3)
    assert rep["lost"] == [1]  # the call at t=10..11 has no trace
    assert rep["by_kind"] == {
        "back_to_back": {"sent": 3, "found": 2},
        "spaced": {"sent": 1, "found": 1},
    }


def test_a_trace_is_never_matched_to_two_calls():
    rep = probe_report([call(0, 1), call(0, 1)], [op_trace("a", 0.5)])
    assert (rep["found"], rep["lost"]) == (1, [1])


def test_other_ops_and_out_of_window_traces_do_not_match():
    rep = probe_report([call(0, 1)], [op_trace("x", 0.5, op="scenario_step"), op_trace("y", 30)])
    assert rep["found"] == 0 and rep["lost"] == [0]


def test_engine_logs_are_fetched_paged_scrubbed_and_written(tmp_path):
    pages = [
        {"entries": [{"timestamp": "2026-01-01T12:00:01Z", "severity": "INFO",
                      "textPayload": 'ACME_DIAG {"event": "op"}'}], "nextPageToken": "t2"},
        {"entries": [{"timestamp": "2026-01-01T12:00:02Z", "severity": "ERROR",
                      "jsonPayload": {"message": "hi Mariana"}}]},
    ]  # fmt: skip

    class S(Session):
        def __init__(self):
            super().__init__([])
            self.bodies = []

        def post(self, url, json=None, timeout=None, **kw):
            self.bodies.append(json)
            return Response(200, pages[len(self.bodies) - 1])

    s = S()
    entries = CloudLogging("acme-airlines-factory", session=s).fetch(
        "projects/p/locations/us-central1/reasoningEngines/123", T0, T0 + timedelta(minutes=5)
    )
    assert [e["severity"] for e in entries] == ["INFO", "ERROR"]
    assert 'reasoning_engine_id="123"' in s.bodies[0]["filter"]
    assert "2026-01-01T12:00:00" in s.bodies[0]["filter"] and s.bodies[1]["pageToken"] == "t2"
    out = tmp_path / "logs.jsonl"
    write_engine_logs(str(out), {"customer": entries}, {"Mariana"})
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows[0]["engine"] == "customer" and "ACME_DIAG" in rows[0]["text"]
    assert "Mariana" not in out.read_text()


def test_log_read_failure_is_recorded_not_fatal(tmp_path):
    class Denied(Session):
        def post(self, *a, **k):
            return Response(403, {})

    cl = CloudLogging("p", session=Denied([]))
    out = tmp_path / "logs.jsonl"
    entries = cl.fetch_or_error("projects/p/locations/l/reasoningEngines/1", T0, T0)
    write_engine_logs(str(out), {"customer": entries}, set())
    assert "log read failed" in out.read_text() and "403" in out.read_text()
