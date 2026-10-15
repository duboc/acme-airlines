"""CI-safe unit tests of the live proof's assertion logic (#167), using fakes, no GCP."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.live.agent_engine_proof import Span, Trace, fixture_pii, run_proof

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeBackend:
    """Two-step scenario; `bad` makes the engine diverge from local mode."""

    def __init__(self, bad=False):
        self.i, self.bad = 0, bad

    def state(self):
        seat = "9Z" if self.bad else "2A"
        return {"scenario_id": "s", "step_index": self.i, "total_steps": 2, "done": self.i == 2,
                "seat": seat}  # fmt: skip

    async def scenario_start(self, sid):
        self.i = 0
        return self.state()

    async def scenario_step(self, sid):
        self.i += 1
        return self.state()


class FakeTraces:
    def __init__(self, traces):
        self.traces, self.calls = traces, 0

    async def list_traces(self, start, end):
        self.calls += 1
        return self.traces


def good(extra=None):
    kinds = ["acme.customer.specialist.seat", "acme.guardrail.decision", "acme.a2a.propose_offer",
             "acme.sales.pitch"]  # fmt: skip
    spans = (Span("scenario_step", {"acme.scenario": "s"}),
             *(Span(k, {"acme.journey_id": "j-1"}) for k in kinds), *(extra or []))  # fmt: skip
    return [
        Trace("abc123", spans),
        Trace("def456", (Span("scenario_step", {"acme.scenario": "s"}),)),
    ]


async def proof(traces, backend=None, **kw):
    async def no_sleep(_):
        pass

    clock = iter(range(0, 10_000, 1))
    return await run_proof(
        backend or FakeBackend(), FakeBackend(), FakeTraces(traces), ("s",), project="p",
        pii={"Mariana", "MSLV42"}, sleep=no_sleep, now=lambda: T0,
        monotonic=lambda: next(clock) * 100.0, ingest_wait=200.0, **kw,
    )  # fmt: skip


async def test_all_good_passes_and_tables_each_turn():
    r = await proof(good())
    assert r.failures == []
    assert [(x.scenario, x.turn) for x in r.rows] == [("s", 1), ("s", 2)]
    assert (
        "| s | 2 | `def456` | [open](https://console.cloud.google.com/traces/list"
        "?tid=def456&project=p) |" in r.markdown()
    )


@pytest.mark.parametrize("kind", ["acme.guardrail.decision", "acme.sales.pitch", "acme.a2a.",
                                  "acme.customer.specialist."])  # fmt: skip
async def test_missing_span_kind_fails(kind):
    t = good()
    t = [Trace("abc123", tuple(s for s in t[0].spans if not s.name.startswith(kind)))]
    r = await proof(t)
    assert any(f"missing span kind '{kind}'" in f for f in r.failures)


async def test_pii_in_an_attribute_value_fails_without_echoing_it():
    r = await proof(good([Span("acme.sales.pitch", {"acme.note": "hi Mariana"})]))
    assert any("PII in span" in f for f in r.failures)
    assert not any("Mariana" in f for f in r.failures)


async def test_missing_step_trace_fails_after_waiting_for_ingestion():
    src = FakeTraces([])
    r = await run_proof(
        FakeBackend(), FakeBackend(), src, ("s",), pii=set(), now=lambda: T0,
        sleep=lambda _: _noop(), monotonic=iter(range(0, 10_000, 100)).__next__, ingest_wait=250.0,
    )  # fmt: skip
    assert src.calls > 1
    assert any("no Customer step trace" in f for f in r.failures)
    assert r.rows == []


async def test_outcome_diverging_from_local_mode_fails():
    r = await proof(good(), backend=FakeBackend(bad=True))
    assert any("!= local" in f for f in r.failures)


def test_fixture_pii_has_the_ac1234_passenger_and_pnr():
    pii = fixture_pii()
    assert "Mariana Silva" in pii and "MSLV42" in pii


async def _noop():
    pass


async def test_polling_backs_off_so_a_missing_trace_costs_few_list_calls():
    """Fixed 10 s polling made 13 list calls per 120 s wait; widening keeps it at 6."""
    sleeps: list[float] = []

    async def record(delay):
        sleeps.append(delay)

    elapsed = {"t": 0.0}

    async def advancing(delay):
        await record(delay)
        elapsed["t"] += delay

    src = FakeTraces([])
    await run_proof(
        FakeBackend(), FakeBackend(), src, ("s",), pii=set(), now=lambda: T0,
        sleep=advancing, monotonic=lambda: elapsed["t"], ingest_wait=120.0,
    )  # fmt: skip
    assert sleeps[:4] == [15.0, 22.5, 33.75, 45.0]  # widening interval, capped at 45 s
    assert max(sleeps) <= 45.0
    # Two turns wait the full 120 s, then the settle pass waits once more: ~39 calls with a fixed
    # 10 s poll, at most 6 per wait (2 turns + settle = 18) now.
    assert src.calls <= 18, f"{src.calls} list calls for two missing-trace waits plus settle"


class TimedTraces:
    """Fake Cloud Trace: each trace is only visible from call number `visible_from[trace_id]` on,
    and only if the queried window covers its `at` instant (like the real API)."""

    def __init__(self, entries):
        self.entries, self.calls, self.windows = entries, 0, []

    async def list_traces(self, start, end):
        self.calls += 1
        self.windows.append((start, end))
        return [t for t, visible_from, at in self.entries
                if self.calls >= visible_from and start <= at <= end]  # fmt: skip


def step(tid, at, extra=()):
    return Trace(tid, (Span("scenario_step", {"acme.scenario": "s"}), *extra))


KINDS = [Span(k) for k in ("acme.customer.specialist.seat", "acme.guardrail.decision",
                           "acme.a2a.propose_offer", "acme.sales.pitch")]  # fmt: skip


class Clock:
    """Fake wall clock advanced only by the fake `sleep`: deterministic, no real waiting."""

    def __init__(self):
        self.t = T0

    def now(self):
        return self.t

    def tick(self, seconds):
        self.t += timedelta(seconds=seconds)


async def settle_proof(src, clock, **kw):
    async def sleep(seconds):
        clock.tick(seconds)

    mono = {"t": 0.0}

    def monotonic():
        mono["t"] += 5.0
        return mono["t"]

    return await run_proof(
        FakeBackend(), FakeBackend(), src, ("s",), project="p", pii=set(),
        sleep=sleep, now=clock.now, monotonic=monotonic, ingest_wait=20.0, settle_wait=300.0, **kw,
    )  # fmt: skip


async def test_a_late_turn_1_trace_is_found_by_the_settle_pass_not_lost():
    clock = Clock()
    # Turn 1's trace only becomes visible long after turn 2 started (call 9); turn 2's is prompt.
    src = TimedTraces([
        (step("t2", T0), 1, T0),
        (step("t1", T0, KINDS), 9, T0),
    ])  # fmt: skip
    r = await settle_proof(src, clock)
    assert r.failures == []
    assert {x.trace_id for x in r.rows} == {"t1", "t2"} and [x.turn for x in r.rows] == [1, 2]
    run_start = src.windows[0][0]
    assert src.windows[-1][0] <= run_start  # the settle pass queried the whole run window


async def test_an_a2a_span_ingested_after_its_root_is_picked_up_in_the_settle_pass():
    clock = Clock()
    kinds_without_a2a = [s for s in KINDS if not s.name.startswith("acme.a2a.")]
    early = step("t1", T0, kinds_without_a2a)
    late = step("t1", T0, KINDS)  # same trace id, now with the a2a span (richer copy wins)
    calls = {"n": 0}

    class Growing:
        calls_ = 0

        async def list_traces(self, start, end):
            calls["n"] += 1
            return [early if calls["n"] < 6 else late, step("t2", T0)]

    r = await settle_proof(Growing(), clock)
    assert r.failures == [] and calls["n"] >= 6


async def test_a_truly_missing_span_is_reported_as_missing_after_settle_with_context():
    clock = Clock()
    kinds_without_a2a = [s for s in KINDS if not s.name.startswith("acme.a2a.")]
    src = TimedTraces([(step("t1", T0, kinds_without_a2a), 1, T0), (step("t2", T0), 1, T0)])
    r = await settle_proof(src, clock)
    (failure,) = r.failures
    assert "missing span kind 'acme.a2a.' after settle" in failure
    assert "2 traces scanned" in failure and "acme.sales.pitch" in failure  # kinds actually seen


async def test_a_truly_missing_turn_trace_is_reported_after_settle_not_dropped_silently():
    clock = Clock()
    src = TimedTraces([(step("t1", T0, KINDS), 1, T0)])  # turn 2 never appears
    r = await settle_proof(src, clock)
    assert [x.turn for x in r.rows] == [1]
    (failure,) = r.failures
    assert "s turn 2: no Customer step trace in Cloud Trace after settle" in failure
    assert "1 traces scanned" in failure


async def test_the_list_call_count_stays_bounded_when_everything_is_prompt():
    clock = Clock()
    src = TimedTraces([(step("t1", T0, KINDS), 1, T0), (step("t2", T0), 1, T0)])
    r = await settle_proof(src, clock)
    assert r.failures == [] and src.calls == 3  # one per turn + one settle listing


# --- #279: forced rebuild ---------------------------------------------------
import json  # noqa: E402

from tests.live.agent_engine_proof import (  # noqa: E402
    Snapshot,
    compare_snapshots,
    conversation,
    duplicate_failures,
    rebuild_events,
    run_rebuild_proof,
)

MSGS = [("outbound", "hello", (), (), "t1"), ("inbound", "hi", (), (), "t2")]


def snap(**kw):
    base = {"state": {"offers_sent": 1, "seat": "2A"}, "journeys": [{"journey_id": "J-1"}],
            "messages": list(MSGS)}  # fmt: skip
    return Snapshot(**{**base, **kw})


def diag_entry(ts="2026-01-01T00:00:05Z", **kw):
    data = {"event": "rebuild", "preset": "p", "to_step": 2, "ms": 41.5, **kw}
    return {"ts": ts, "severity": "INFO", "text": "ACME_DIAG " + json.dumps(data)}


class RebuildBackend:
    """Engine stand-in: a plain run of `p`, then a rebuild with optional faults."""

    def __init__(self, drift=None, extra_message=False, extra_offer=False):
        self.drift, self.extra_message, self.extra_offer = drift, extra_message, extra_offer
        self.rebuilt, self._active, self._step = False, None, 0

    async def scenario_start(self, sid):
        self._active, self._step = sid, 0
        return {"done": False, "step_index": 0, "offers_sent": 0}

    async def scenario_step(self, sid):
        self._step += 1
        return {"done": self._step == 2, "step_index": self._step, "offers_sent": 1, "seat": "2A"}

    async def scenario_state(self):
        self.rebuilt = self._active == "p" and self._step == 2
        offers = 2 if self.extra_offer and self.rebuilt else 1
        return {"done": True, "step_index": 2, "offers_sent": offers,
                "seat": self.drift or "2A"}  # fmt: skip

    async def journeys_list(self):
        return [{"journey_id": "J-1", "passenger_id": "P-1"}]

    async def api(self, op, **kw):
        msgs = [{"direction": d, "text": t, "timestamp": ts, "message_id": f"WM-{i}{self.rebuilt}"}
                for i, (d, t, _b, _a, ts) in enumerate(MSGS)]  # fmt: skip
        return msgs + (msgs[:1] if self.extra_message and self.rebuilt else [])


async def rebuild(backend, entries):
    async def no_sleep(_):
        pass

    clock = iter(range(0, 10_000))
    return await run_rebuild_proof(
        backend, lambda since: entries, "p", "d", sleep=no_sleep, now=lambda: T0,
        monotonic=lambda: float(next(clock)), wait=3.0, poll=1.0,
    )  # fmt: skip


def test_conversation_ignores_the_random_message_id():
    a = [{"message_id": "WM-1", "direction": "outbound", "text": "x"}]
    assert conversation(a) == conversation([{**a[0], "message_id": "WM-2"}])


def test_compare_flags_each_difference_without_echoing_content():
    assert compare_snapshots(snap(), snap()) == []
    out = compare_snapshots(snap(), snap(state={"seat": "9Z"}, messages=MSGS[:1]))
    assert len(out) == 2 and "1 entries, expected 2" in out[1]
    assert "9Z" not in " ".join(out)
    assert "same size" in compare_snapshots(snap(), snap(journeys=[{"journey_id": "J-2"}]))[0]


def test_duplicate_check_flags_repeated_message_extra_message_and_extra_offer():
    assert duplicate_failures(snap(), snap()) == []
    out = duplicate_failures(snap(), snap(messages=[*MSGS, MSGS[0]], state={"offers_sent": 2}))
    assert any("duplicate WhatsApp message" in f for f in out)
    assert any("1 more WhatsApp" in f for f in out)
    assert any("duplicate offer" in f for f in out)


def test_rebuild_events_filter_by_preset_step_and_time():
    entries = [diag_entry(), diag_entry(preset="q"), diag_entry(to_step=1),
               diag_entry(ts="2025-12-31T23:00:00Z"), {"text": "other"}]  # fmt: skip
    assert [e["ms"] for e in rebuild_events(entries, "p", 2, T0)] == [41.5]


async def test_rebuild_proof_passes_and_reports_latency():
    r = await rebuild(RebuildBackend(), [diag_entry()])
    assert r.failures == [] and r.engine_ms == [41.5] and r.to_step == 2
    md = r.markdown()
    assert "PASS" in md and "41.5 ms" in md and "client side" in md


@pytest.mark.parametrize(
    ("backend", "entries", "needle"),
    [
        (RebuildBackend(drift="9Z"), [diag_entry()], "state differs"),
        (RebuildBackend(extra_message=True), [diag_entry()], "duplicate WhatsApp message"),
        (RebuildBackend(extra_offer=True), [diag_entry()], "duplicate offer"),
        (RebuildBackend(), [], "no ACME_DIAG rebuild event"),
        (RebuildBackend(), [diag_entry(), diag_entry()], "2 rebuilds logged"),
        (RebuildBackend(), [diag_entry(ms=None)], "no latency"),
    ],
)
async def test_rebuild_proof_failures(backend, entries, needle):
    r = await rebuild(backend, entries)
    assert any(needle in f for f in r.failures), r.failures
    assert "FAIL" in r.markdown()


async def test_forced_rebuild_against_the_real_engines_offline(monkeypatch):
    """The real Customer and Sales engines (fake LLM), the forced call included: it must rebuild."""
    from tests.api.test_engine_mode import engine_backend

    monkeypatch.setenv("LLM_MODE", "fake")
    backend, captured = engine_backend(), []
    monkeypatch.setattr("acme.engines.customer.diag",
                        lambda event, **kw: captured.append({"event": event, **kw}))  # fmt: skip

    def logs(since):
        return [{"ts": datetime.now(UTC).isoformat(), "text": "ACME_DIAG " + json.dumps(c)}
                for c in captured if c["event"] == "rebuild"]  # fmt: skip

    r = await run_rebuild_proof(backend, logs, "happy-path", "seat-taken", wait=0.0)
    assert r.failures == [] and len(r.engine_ms) == 1 and r.to_step == 5
