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


def test_engine_logs_are_fetched_with_paging_and_the_run_window(tmp_path):
    pages = [
        {"entries": [{"timestamp": "2026-01-01T12:00:01Z", "severity": "INFO",
                      "textPayload": 'ACME_DIAG {"event": "op"}'}], "nextPageToken": "t2"},
        {"entries": [{"timestamp": "2026-01-01T12:00:02Z", "severity": "ERROR",
                      "jsonPayload": {"message": "x"}}]},
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


GOOD_OP = {
    "event": "op", "op": "journeys_list", "trace_id": "a" * 32, "span_id": "b" * 16,
    "is_recording": True, "sampled": True, "parent": {"valid": True, "sampled": False},
    "flush_ok": True, "flush_ms": 12.5, "flush_error": None, "pid": 41,
    "instance": "abc123-host", "provider": "TracerProvider", "ts": "2026-01-01T12:00:01+00:00",
}  # fmt: skip


def diag(record, severity="INFO", ts="2026-01-01T12:00:01Z"):
    return {"ts": ts, "severity": severity, "text": "ACME_DIAG " + json.dumps(record)}


def written(tmp_path, entries):
    out = tmp_path / "logs.jsonl"
    write_engine_logs(str(out), {"customer": entries})
    return out.read_text(), [json.loads(line) for line in out.read_text().splitlines()]


def test_only_allowlisted_acme_diag_fields_survive(tmp_path):
    entries = [
        diag(GOOD_OP),
        diag(
            {
                "event": "export",
                "ok": False,
                "n": 7,
                "ms": 80.1,
                "error": "ConnectionError",
                "ts": "x",
            }
        ),  # fmt: skip
        diag({**GOOD_OP, "note": "hello Mariana Silva", "payload": {"phone": "+55 11 91234-5678"}}),
    ]
    text, rows = written(tmp_path, entries)
    assert rows[0] == {
        "engine": "customer", "event": "op", "severity": "INFO", "ts": "2026-01-01T12:00:01Z",
        "op": "journeys_list", "trace_id": "a" * 32, "span_id": "b" * 16, "is_recording": True,
        "sampled": True, "parent_valid": True, "parent_sampled": False, "flush_ok": True,
        "flush_ms": 12.5, "pid": 41, "instance": "abc123-host", "provider": "TracerProvider",
    }  # fmt: skip
    assert rows[1]["event"] == "export" and rows[1]["error"] == "ConnectionError"
    assert "note" not in rows[2] and "payload" not in rows[2]
    assert "Mariana" not in text and "91234" not in text


def test_pii_tracebacks_bearer_tokens_and_container_stderr_never_survive(tmp_path):
    hostile = [
        {"ts": "t", "severity": "ERROR", "text": "Traceback (most recent call last):\n  File x\n"
         "ValueError: passenger Mariana Silva MSLV42 cannot be rebooked"},
        {"ts": "t", "severity": "INFO", "text": "Authorization: Bearer ya29.A0ARrdaM-secret-token"},
        {"ts": "t", "severity": "WARNING", "text": "mariana@example.com +55 11 91234-5678"},
        {"ts": "t", "severity": "INFO", "text": "GET /api POST body {pnr: MSLV42}"},
        {"ts": "t", "severity": "ERROR", "text": 'prefix ACME_DIAG {"event": "op"}'},  # mid-line
        {"ts": "t", "severity": "INFO", "text": "ACME_DIAG {not json Mariana"},
        # a DIAG line whose VALUES try to smuggle text through allowlisted keys
        diag({**GOOD_OP, "op": "hi Mariana Silva!", "trace_id": "Bearer abc", "instance": "a b",
              "provider": "x" * 80, "flush_error": "boom: secret", "pid": "41; drop"}),
    ]  # fmt: skip
    text, rows = written(tmp_path, hostile)
    for banned in ("Mariana", "MSLV42", "ya29", "Bearer", "example.com", "91234", "Traceback",
                   "secret", "; drop", "Authorization", "POST"):  # fmt: skip
        assert banned not in text, banned
    ops = [r for r in rows if r.get("event") == "op"]
    assert len(ops) == 1  # the smuggling line survives only with its valid fields
    assert set(ops[0]) <= {
        "engine", "event", "severity", "ts", "trace_id", "span_id", "is_recording", "sampled",
        "parent_valid", "parent_sampled", "flush_ok", "flush_ms", "flush_error", "pid",
        "instance", "provider", "op",
    }  # fmt: skip
    assert "op" not in ops[0] and "trace_id" not in ops[0] and "pid" not in ops[0]


def test_dropped_lines_are_counted_never_quoted(tmp_path):
    entries = [diag(GOOD_OP)] + [
        {"ts": "t", "severity": "ERROR", "text": "anything at all"},
        {"ts": "t", "severity": "INFO", "text": "something else"},
    ]
    _, rows = written(tmp_path, entries)
    summary = [r for r in rows if r.get("event") == "dropped"]
    assert summary == [{"engine": "customer", "event": "dropped", "lines": 2, "errors": 1}]


def test_log_read_failure_is_recorded_not_fatal(tmp_path):
    class Denied(Session):
        def post(self, *a, **k):
            return Response(403, {})

    cl = CloudLogging("p", session=Denied([]))
    out = tmp_path / "logs.jsonl"
    entries = cl.fetch_or_error("projects/p/locations/l/reasoningEngines/1", T0, T0)
    write_engine_logs(str(out), {"customer": entries})
    (row,) = [json.loads(line) for line in out.read_text().splitlines()]
    assert row == {"engine": "customer", "event": "log_read_failed", "error": "HTTPError"}
