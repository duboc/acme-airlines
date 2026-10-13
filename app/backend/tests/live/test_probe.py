"""#221 unary probe report, offline."""

from datetime import UTC, datetime, timedelta

from tests.live.agent_engine_proof import Span, Trace, parse_ts, probe_report

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
