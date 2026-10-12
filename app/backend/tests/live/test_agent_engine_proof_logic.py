"""CI-safe unit tests of the live proof's assertion logic (#167), using fakes, no GCP."""

from datetime import UTC, datetime

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
    return [Trace("abc123", spans)]


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
        "| s | 2 | `abc123` | [open](https://console.cloud.google.com/traces/list"
        "?tid=abc123&project=p) |" in r.markdown()
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
