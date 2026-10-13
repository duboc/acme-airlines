"""Trace-inventory diagnostic artifact (#219): offline, fake backend/traces, no PII."""

import json
from datetime import UTC, datetime

from tests.live.agent_engine_proof import (
    CloudTraceV1,
    Span,
    Trace,
    run_proof,
    write_inventory,
)
from tests.live.test_agent_engine_proof_logic import KINDS, FakeBackend
from tests.live.test_cloud_trace_retry import Response, Session

T0 = datetime(2026, 1, 1, tzinfo=UTC)
PII = {"Mariana", "MSLV42"}


class SessionBackend(FakeBackend):
    def state(self):
        return {**super().state(), "session_id": "sess-1"}


def step_span(dirty=False):
    attrs = {"acme.scenario": "s", "http.url": "https://x/path"}
    if dirty:  # personal data in an acme.* value and in a non-acme value
        attrs |= {"acme.note": "hi Mariana", "http.target": "/Mariana"}
    return Span(
        "acme.scenario.step", attrs, "span-1", "", "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z"
    )


class Source:
    def __init__(self, filtered, unfiltered, fail_probe=False):
        self.filtered, self.unfiltered, self.fail_probe = filtered, unfiltered, fail_probe
        self.listings = [{"filter": "span:acme.", "pages": 1, "traces": len(filtered)}]
        self.probed = 0

    async def list_traces(self, start, end):
        return self.filtered

    async def list_unfiltered(self, start, end):
        self.probed += 1
        if self.fail_probe:
            raise RuntimeError("boom")
        return self.unfiltered


async def run(src, **kw):
    async def no_sleep(_):
        pass

    return await run_proof(
        SessionBackend(), SessionBackend(), src, ("s",), project="p", pii=PII, sleep=no_sleep,
        now=lambda: T0, monotonic=iter(range(0, 100_000, 100)).__next__,
        ingest_wait=200.0, settle_wait=200.0, **kw,
    )  # fmt: skip


def good_traces(dirty=False):
    child = Span("acme.guardrail.detail", {"acme.journey_id": "J-1"}, "span-2", "span-1")
    others = [Span(k.name, {}, f"s{i}", "span-1") for i, k in enumerate(KINDS)]
    return [Trace("t1", (step_span(dirty), child, *others)), Trace("t2", (step_span(dirty),))]


async def test_no_inventory_on_success_unless_dump_is_requested():
    src = Source(good_traces(), [])
    assert (await run(src)).inventory is None and src.probed == 0
    r = await run(src, dump=True)
    assert r.inventory is not None and src.probed == 1


async def test_a_failing_run_always_carries_the_inventory():
    src = Source([Trace("t1", (step_span(),))], [Trace("noise", (Span("/check"),))])
    r = await run(src)
    assert r.failures and r.inventory is not None
    assert [t["trace_id"] for t in r.inventory["unfiltered_traces"]] == ["noise"]
    assert [t["trace_id"] for t in r.inventory["filtered_traces"]] == ["t1"]


async def test_inventory_records_turn_timestamps_session_and_matched_trace():
    r = await run(Source(good_traces(), []), dump=True)
    first, second = r.inventory["turns"]
    assert (first["scenario"], first["turn"], first["session_id"]) == ("s", 1, "sess-1")
    assert {"sent_at", "received_at", "window_start"} <= set(first)
    assert (first["matched_trace_id"], second["matched_trace_id"]) == ("t1", "t2")


async def test_inventory_records_span_times_parents_and_acme_attribute_values():
    r = await run(Source(good_traces(), []), dump=True)
    spans = {s["name"]: s for s in r.inventory["filtered_traces"][0]["spans"]}
    step, guard = spans["acme.scenario.step"], spans["acme.guardrail.detail"]
    assert (step["start"], step["end"], step["span_id"]) == (
        "2026-01-01T00:00:01Z",
        "2026-01-01T00:00:02Z",
        "span-1",
    )
    assert guard["parent_id"] == "span-1" and guard["acme"] == {"acme.journey_id": "J-1"}
    assert "http.url" in step["attr_keys"] and "http.url" not in step["acme"]  # keys only


async def test_the_inventory_json_contains_no_pii():
    r = await run(Source(good_traces(dirty=True), good_traces(dirty=True)), dump=True)
    assert any("PII in span" in f for f in r.failures)  # the proof itself flags it too
    dumped = json.dumps(r.inventory)
    assert "Mariana" not in dumped and "MSLV42" not in dumped
    assert "[redacted]" in dumped  # the acme.note value was scrubbed, not dropped silently


async def test_a_failing_unfiltered_probe_is_reported_but_never_masks_the_run(tmp_path):
    r = await run(Source(good_traces(), [], fail_probe=True), dump=True)
    assert any("diagnostic unfiltered listing failed" in f for f in r.failures)
    write_inventory(r, str(tmp_path / "inv.json"))
    assert json.loads((tmp_path / "inv.json").read_text())["unfiltered_traces"] is None


def _page(trace_id, token=None):
    span = {"name": "acme.x", "spanId": "9", "parentSpanId": "8", "startTime": "a", "endTime": "b",
            "labels": {"acme.k": "v"}}  # fmt: skip
    return Response(200, {"traces": [{"traceId": trace_id, "spans": [span]}],
                          **({"nextPageToken": token} if token else {})})  # fmt: skip


def test_cloud_trace_records_each_listing_with_and_without_the_filter():
    session = Session(_page("a", "n"), _page("b"), _page("noise"))
    c = CloudTraceV1("p", session=session, sleep=lambda _: None)
    start, end = T0, T0.replace(hour=1)
    filtered = c._list(start, end)
    unfiltered = c._list(start, end, "")
    assert [x.trace_id for x in filtered] == ["a", "b"]
    assert len(unfiltered) == 1
    assert [(r["filter"], r["pages"], r["traces"]) for r in c.listings] == [
        ("span:acme.", 2, 2), ("", 1, 1)]  # fmt: skip
    sp = filtered[0].spans[0]
    assert (sp.span_id, sp.parent_id, sp.start, sp.end) == ("9", "8", "a", "b")
