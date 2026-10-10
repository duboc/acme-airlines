from datetime import timedelta

import pytest

from acme.customer.runtime import CustomerRuntime
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT, GRU
from acme.domain.journey import DisruptionStatus as S


@pytest.fixture
def rt():
    return CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT))


async def test_cancellation_creates_resolving_journey_with_profile(rt):
    ids = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    assert len(ids) == 1
    state = rt.journeys[ids[0]].state
    assert state.status == S.RESOLVING
    types = [e.type for e in state.timeline]
    assert types[0] == "cancellation_detected"
    assert "profile_loaded" in types
    profile = next(e for e in state.timeline if e.type == "profile_loaded").payload
    assert (profile["tier"], profile["seat"], profile["bags"]) == ("diamond", "14A", 1)
    assert profile["connection"]["flight"] == "AC0800"
    assert state.timeline[0].timestamp == DISRUPTION_DETECTED_AT


async def test_constraint_is_latest_scl_arrival_1230(rt):
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    c = next(e for e in rt.journeys[jid].state.timeline if e.type == "connection_constraint")
    assert c.payload["airport"] == "SCL" and c.payload["mct_minutes"] == 90
    assert c.payload["latest_arrival"].startswith("2026-10-10T12:30")
    assert c.payload["text"] == "must arrive SCL by 2026-10-10 12:30"
    latest = rt.journeys[jid].constraint_latest_arrival
    assert latest.astimezone(GRU).strftime("%H:%M") == "12:30"


async def test_silent_at_2205(rt):
    await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    rt.clock.advance(timedelta(minutes=2))
    assert rt.messages == {}


async def test_flight_without_acme_passengers_creates_no_journey(rt):
    assert await rt.trigger_cancellation("AC1240", "crew") == []
    assert rt.journeys == {}


async def test_same_event_is_not_processed_twice(rt):
    await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    assert await rt.poll(timeout_s=0) == []
    assert len(rt.journeys) == 1
    (jid,) = rt.journeys
    detected = [e for e in rt.journeys[jid].state.timeline if e.type == "cancellation_detected"]
    assert len(detected) == 1


async def test_unknown_flight_raises(rt):
    with pytest.raises(KeyError):
        await rt.trigger_cancellation("ZZ999", "x")


async def test_passenger_without_onward_segment_has_no_constraint(rt):
    pnr = rt.sim.world.pnrs["MSLV42"]
    pnr.segments = pnr.segments[:1]
    (jid,) = await rt.trigger_cancellation("AC1234", "x")
    profile = next(e for e in rt.journeys[jid].state.timeline if e.type == "profile_loaded")
    assert profile.payload["connection"] is None
    assert rt.journeys[jid].constraint_latest_arrival is None
