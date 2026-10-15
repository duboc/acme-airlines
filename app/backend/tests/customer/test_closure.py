"""#416: verify_journey handles zero checked bags and checks economy cabin on un-upgraded seats."""

from datetime import timedelta

import pytest

from acme.customer.conversion import close_loop
from acme.customer.runtime import CustomerRuntime
from acme.customer.verify import verify_journey
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus as S
from acme.domain.models import SeatAssignment
from acme.sim.dcs import dcs_state

PNR = "MSLV42"


@pytest.fixture
async def resolved_journey():
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT))
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    rt.clock.advance(timedelta(minutes=7))
    await rt.tick()
    await rt.handle_inbound("P-MARIANA", "Thanks, that all works for me!")
    rec = rt.journeys[jid]
    assert rec.state.status is S.RESOLVED and rec.state.resolution_acknowledged
    return rt, rec


async def test_ac1_zero_checked_bags_passes_baggage_verification_and_closes(resolved_journey):
    rt, rec = resolved_journey
    rt.world.pnrs[PNR].bags = []
    res = await verify_journey(rt, rec)
    assert res.checks["baggage"] is True and res.ok
    await close_loop(rt, rec)
    assert rec.state.status is S.CLOSED
    assert any(e.type == "verification_passed" for e in rec.state.timeline)


async def test_ac2_wrong_bag_routing_or_missing_flight_fails_baggage_check(resolved_journey):
    rt, rec = resolved_journey
    tag = rt.world.pnrs[PNR].bags[0].tag
    dcs_state(rt.sim).bag_routing[tag] = "AC1252"  # wrong flight (journey is on AC1240)
    res = await verify_journey(rt, rec)
    assert res.checks["baggage"] is False
    assert "expected bags routed to AC1240, found ['AC1252']" in res.details["baggage"]

    # Also fails when flight is None (no rebooked event)
    rt.world.pnrs[PNR].bags = []
    rec.state._timeline[:] = [e for e in rec.state.timeline if e.type != "rebooked"]  # noqa: SLF001
    res_no_flight = await verify_journey(rt, rec)
    assert res_no_flight.checks["baggage"] is False


async def test_ac3_unupgraded_seat_requires_economy_cabin(resolved_journey):
    rt, rec = resolved_journey
    res_ok = await verify_journey(rt, rec)
    assert res_ok.checks["seat"] is True

    rt.world.pnrs[PNR].seats = [
        SeatAssignment(flight_number="AC1240", seat="14A", cabin="premium_business")
    ]
    res_bad = await verify_journey(rt, rec)
    assert res_bad.checks["seat"] is False
