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
