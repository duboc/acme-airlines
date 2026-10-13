"""Cross-process demo state, part 1 (#200): JSON snapshots of the whole runtime."""

import json
from datetime import datetime, timedelta

import pytest

from acme.customer.conversion import accept_offer
from acme.customer.runtime import CustomerRuntime
from acme.customer.skills import CustomerSkills
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT, GRU
from acme.llm.fake import FakeLlm
from acme.memory.store import SqliteJourneyStore
from acme.state.snapshot import capture, restore

PID = "P-MARIANA"


def fresh():
    return CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())


async def drive_to_accepted(rt):
    """Scenario 1 -> 5: cancel, rebuild, notify, replan, acknowledge, offer, accept."""
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    rt.clock.advance(datetime(2026, 10, 9, 22, 9, tzinfo=GRU) - rt.clock.now())
    await rt.tick()
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    await rt.handle_inbound(PID, "Perfect, thanks!")
    from acme.sales.radar import SalesRadar
    from acme.sim.client import SimClient, inprocess_connector

    class Port:
        async def list_journeys(self):
            return CustomerSkills(rt).list_journeys()

        async def journey_status(self, jid):
            return CustomerSkills(rt).journey_status(jid).model_dump()

        async def propose_offer(self, offer):
            return await CustomerSkills(rt).propose_and_deliver(offer.model_dump(mode="json"))

    await SalesRadar(Port(), SimClient(inprocess_connector(rt.sim, "sales"))).tick()
    await accept_offer(rt, rt.journeys[jid])
    return jid


def timeline(rt, jid):
    return [e.model_dump(mode="json") for e in rt.journeys[jid].state.timeline]


async def test_full_runtime_round_trips_through_json_into_a_fresh_runtime(tmp_path):
    a = fresh()
    jid = await drive_to_accepted(a)
    store = SqliteJourneyStore(str(tmp_path / "s.db"))
    text = capture(a, store)
    json.loads(text)  # plain JSON, not a pickle

    b = fresh()
    restore(b, text, store)
    assert timeline(b, jid) == timeline(a, jid)
    assert b.journeys[jid].state.status == a.journeys[jid].state.status
    assert [o.status for o in b.journeys[jid].state.offers] == ["accepted"]
    assert b.messages[PID] == a.messages[PID]
    assert b.world.model_dump() == a.world.model_dump()
    assert b.sim.partners.charges == a.sim.partners.charges
    assert b.journeys[jid].hotel == a.journeys[jid].hotel
    assert b.journeys[jid].vouchers == a.journeys[jid].vouchers
    assert b.journeys[jid].run_id == a.journeys[jid].run_id
    assert b.clock.now() == a.clock.now() and b.sim.clock is b.clock
    assert capture(b, store) == text  # stable: restoring then capturing changes nothing


async def test_restored_runtime_keeps_working(tmp_path):
    a = fresh()
    jid = await drive_to_accepted(a)
    store = SqliteJourneyStore(str(tmp_path / "s.db"))
    b = fresh()
    restore(b, capture(a, store), store)
    from acme.sim.dcs import dcs_state

    assert dcs_state(b.sim).bag("BAG0001")["routing"] == "AC1252"
    assert any(s.seat == "2A" for s in b.world.pnrs["MSLV42"].seats)
    await b.handle_inbound(PID, "hello")  # the restored runtime handles new input
    assert "passenger_message" in [e.type for e in b.journeys[jid].state.timeline[-3:]]


async def test_restore_replaces_state_in_place_and_keeps_object_identity(tmp_path):
    rt = fresh()
    sim_before, journeys_before = rt.sim, rt.journeys
    other = fresh()
    await other.trigger_cancellation("AC1234", "x")
    store = SqliteJourneyStore(str(tmp_path / "s.db"))
    restore(rt, capture(other, store), store)
    assert rt.sim is sim_before and rt.journeys is journeys_before and len(rt.journeys) == 1


async def test_restore_removes_journeys_that_no_longer_exist(tmp_path):
    a, b = fresh(), fresh()
    await b.trigger_cancellation("AC1234", "x")
    store = SqliteJourneyStore(str(tmp_path / "s.db"))
    restore(b, capture(a, store), store)  # a has no journeys (e.g. after a reset)
    assert b.journeys == {} and store.ids() == []


def test_capture_is_pure_json_and_never_pickles():
    import acme.state.snapshot as mod

    src = open(mod.__file__).read()
    assert "pickle" not in src


async def test_scenario_cursor_round_trips(tmp_path):
    from acme.api.scenarios import PRESETS, ScenarioRunner

    a = fresh()
    sa = ScenarioRunner(a)
    sa.start(PRESETS[0])
    await sa.step(sales_tick=False)
    store = SqliteJourneyStore(str(tmp_path / "s.db"))
    text = capture(a, store, sa)
    b = fresh()
    sb = ScenarioRunner(b)
    restore(b, text, store, sb)
    assert sb.active.id == PRESETS[0].id and sb.index == 1
    assert sb.state().step_index == 1
    assert isinstance(b.clock, FixedClock)
    assert b.clock.now() >= DISRUPTION_DETECTED_AT + timedelta(0)


def test_garbage_snapshot_is_rejected_cleanly(tmp_path):
    rt = fresh()
    store = SqliteJourneyStore(str(tmp_path / "s.db"))
    with pytest.raises(ValueError):
        restore(rt, "{not json", store)
