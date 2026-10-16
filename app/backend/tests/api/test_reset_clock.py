"""Reset and manual trigger after a preset (#321): the fixed clock and the cursor are gone."""

import json

import httpx

from acme.api.app import create_app
from acme.api.engine_backend import create_engine_app
from acme.api.scenarios import PRESETS, ScenarioRunner
from acme.customer.runtime import CustomerRuntime
from acme.domain.clock import FixedClock, SystemClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.llm.fake import FakeLlm
from acme.memory.store import SqliteJourneyStore
from acme.state.snapshot import capture, restore
from tests.api.test_engine_mode import LocalEngine, engine_backend  # noqa: F401

FROZEN = DISRUPTION_DETECTED_AT.isoformat()


def first_stamp(sse: str) -> str:
    data = next(ln for ln in sse.splitlines() if ln.startswith("data:"))
    return json.loads(data[5:])["timestamp"]


TRIGGER = {"flight": "AC1234", "reason": "mechanical"}


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_ac1_reset_restores_the_default_clock():
    rt = CustomerRuntime(llm=FakeLlm())
    ScenarioRunner(rt).start(PRESETS[0])
    assert isinstance(rt.clock, FixedClock)
    rt.reset()
    assert isinstance(rt.clock, SystemClock) and rt.sim.clock is rt.clock


def test_ac1_reset_keeps_an_injected_clock():
    injected = FixedClock(DISRUPTION_DETECTED_AT)
    rt = CustomerRuntime(injected, llm=FakeLlm())
    rt.reset(FixedClock(DISRUPTION_DETECTED_AT))
    rt.reset()
    assert rt.clock is injected and rt.sim.clock is injected


def test_ac2_restore_of_a_post_reset_snapshot_drops_a_stale_fixed_clock(tmp_path):
    store = SqliteJourneyStore(str(tmp_path / "s.db"))
    text = capture(CustomerRuntime(llm=FakeLlm()), store)  # a reset world: no fixed clock
    worker = CustomerRuntime(llm=FakeLlm())
    ScenarioRunner(worker).start(PRESETS[0])
    restore(worker, text, store)
    assert isinstance(worker.clock, SystemClock) and worker.sim.clock is worker.clock


async def test_ac3_local_reset_and_trigger_use_the_live_clock():
    app = create_app()
    async with client(app) as c:
        for ending in ("reset", "trigger"):
            assert (await c.post("/scenarios/happy-path/start")).status_code == 200
            if ending == "reset":
                assert (await c.post("/scenario/reset")).status_code == 200
            r = await c.post("/scenario/trigger-cancellation", json=TRIGGER)
            jid = r.json()["journey_ids"][0]
            events = (await c.get(f"/journeys/{jid}/events?limit=1")).text
            assert first_stamp(events) != FROZEN
            await c.post("/scenario/reset")


async def test_ac3_engine_reset_clears_the_cursor_and_the_clock():
    backend = engine_backend()
    customer = backend._customer.app  # noqa: SLF001
    async with client(create_engine_app(backend)) as c:
        await c.post("/scenarios/happy-path/start")
        await c.post("/scenarios/happy-path/step")
        await c.post("/scenario/reset")
        assert (backend._active, backend._step, backend._tape) == (None, 0, None)  # noqa: SLF001
        assert customer.scenarios.active is None and customer.scenarios.index == 0
        assert isinstance(customer.runtime.clock, SystemClock)
        r = await c.post("/scenario/trigger-cancellation", json=TRIGGER)
        jid = r.json()["journey_ids"][0]
        events = (await c.get(f"/journeys/{jid}/events?limit=1")).text
        assert first_stamp(events) != FROZEN


async def test_ac3_engine_trigger_during_a_preset_ends_it():
    backend = engine_backend()
    customer = backend._customer.app  # noqa: SLF001
    async with client(create_engine_app(backend)) as c:
        await c.post("/scenarios/happy-path/start")
        assert backend._active == "happy-path"  # noqa: SLF001
        r = await c.post("/scenario/trigger-cancellation", json=TRIGGER)
        assert r.status_code == 200
        assert (backend._active, backend._step, backend._tape) == (None, 0, None)  # noqa: SLF001
        assert customer.scenarios.active is None
        assert isinstance(customer.runtime.clock, SystemClock)


# --- a secondary cancellation after a preset step keeps the journey (#485) ---------------------
SECONDARY = {"flight": "AC1240", "reason": "severe weather"}


async def started_and_stepped(c, sid="happy-path", steps=1):
    await c.post(f"/scenarios/{sid}/start")
    for _ in range(steps):
        state = (await c.post(f"/scenarios/{sid}/step")).json()
    return state["journey_id"]


async def test_ac1_a_secondary_trigger_keeps_the_open_journey_and_ends_the_preset():
    app = create_app()
    rt = app.state.runtime
    async with client(app) as c:
        jid = await started_and_stepped(c)  # Mariana is RESOLVED on AC1240, fixed clock
        r = await c.post("/scenario/trigger-cancellation", json=SECONDARY)
        assert r.status_code == 200 and r.json() == {"journey_ids": [jid]}  # same journey
        d = (await c.get(f"/journeys/{jid}")).json()  # never a 404
        assert d["itinerary"][0]["flight_number"] == "AC1252"
        assessed = [e for e in rt.journeys[jid].state.timeline if e.type == "duty_of_care_assessed"]
        assert assessed[-1].payload["compensation_eligible"] is False  # DOC-11 force majeure
        assert isinstance(rt.clock, SystemClock) and rt.sim.clock is rt.clock
        assert all(rec.state._clock is rt.clock for rec in rt.journeys.values())  # noqa: SLF001
        # the preset is over: its cursor is gone
        assert (await c.post("/scenarios/happy-path/step")).status_code == 409


async def test_ac1_a_primary_trigger_during_a_preset_still_resets_everything():
    app = create_app()
    rt = app.state.runtime
    async with client(app) as c:
        jid = await started_and_stepped(c)
        r = await c.post(
            "/scenario/trigger-cancellation", json={"flight": "AC1234", "reason": "mechanical"}
        )
        assert r.status_code == 200
        assert isinstance(rt.clock, SystemClock) and rt.journeys[jid].state.timeline[
            0
        ].timestamp != (DISRUPTION_DETECTED_AT)


async def test_ac1_a_secondary_trigger_with_no_journey_is_a_full_reset():
    rt = CustomerRuntime(llm=FakeLlm())
    runner = ScenarioRunner(rt)
    runner.start(PRESETS[0])  # seeded, no journey yet
    assert not rt.journeys
    runner.end_preset("AC1240")
    assert runner.active is None and isinstance(rt.clock, SystemClock)


async def test_ac3_a_manual_reply_ticks_sales_without_a_preset_in_local_mode():
    app = create_app()
    async with client(app) as c:
        jid = await started_and_stepped(c)
        await c.post("/scenario/trigger-cancellation", json=SECONDARY)  # the preset is over
        r = await c.post("/whatsapp/P-MARIANA/messages", json={"text": "Perfect, thanks!"})
        assert r.status_code == 201
        d = (await c.get(f"/journeys/{jid}")).json()
        assert d["offer"] is not None and d["offer"]["status"] == "sent"  # Sales ticked


async def test_ac2_the_engine_backend_sends_the_preset_context_on_a_secondary_trigger():
    from tests.api.test_engine_mode import engine_backend

    backend = engine_backend()
    seen = []
    real = backend._customer.call  # noqa: SLF001

    async def spy(op, **kwargs):
        if op == "trigger_cancellation":
            seen.append(kwargs)
        return await real(op, **kwargs)

    backend._customer.call = spy  # noqa: SLF001
    async with client(create_engine_app(backend)) as c:
        jid = await started_and_stepped(c)
        r = await c.post("/scenario/trigger-cancellation", json=SECONDARY)
        assert r.status_code == 200 and r.json() == {"journey_ids": [jid]}
        (kw,) = seen
        assert kw["preset"] == "happy-path" and kw["step"] == 1  # so a fresh worker can rebuild
        assert (backend._active, backend._step, backend._tape) == (None, 0, None)  # noqa: SLF001
        # a primary trigger sends none: it resets the world anyway
        await started_and_stepped(c)
        await c.post("/scenario/trigger-cancellation", json=TRIGGER)
        assert "preset" not in seen[-1]
