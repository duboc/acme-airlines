"""#229: service closure is independent of the revenue path ("Two Goals, One Guardrail").

A RESOLVED, acknowledged journey is verified and CLOSED (CRM log, NPS prompt, analytics, Memory
Bank commit) once the offer window passes, whether or not an offer was ever made. It never waits
on Sales."""

from datetime import timedelta

import pytest

from acme.customer.skills import CustomerSkills, SkillError
from acme.domain.journey import DisruptionStatus as S
from tests.governance.flow import Flow, offer_payload

PID = "P-MARIANA"
CLOSURE_EVENTS = ("verification_passed", "crm_logged", "nps_scheduled", "analytics_recorded",
                  "memory_committed")  # fmt: skip


def types(flow):
    return [e.type for e in flow.state.timeline]


async def after_window(flow, extra=timedelta(minutes=1)):
    flow.rt.clock.advance(flow.rt.offer_window + extra)
    await flow.rt.tick()


def assert_closed_without_an_offer(flow):
    assert flow.state.status is S.CLOSED
    t = types(flow)
    for event in CLOSURE_EVENTS:
        assert t.count(event) == 1, (event, t)
    assert t.index("verification_passed") < t.index("crm_logged") < t.index("memory_committed")
    analytics = flow.events("analytics_recorded")[0].payload
    assert analytics["converted"] is False and analytics["event"] == "no_offer"
    assert flow.events("service_closure")[0].payload == {"reason": "offer_window_elapsed"}
    assert flow.rt.store.load(flow.state.journey_id) is not None  # committed to the store
    assert not flow.rt.sim.partners.charges  # nothing was charged


async def acknowledged_on_ac1240(flow):
    """Cancel, notify, acknowledge on the first rebooking (no replan)."""
    await flow.cancel()
    await flow.notify()
    await flow.rt.handle_inbound(PID, "Perfect, thanks!")  # no radar tick: Sales may be down


async def test_sales_unavailable_the_journey_still_closes_after_the_window():
    f = Flow()
    await acknowledged_on_ac1240(f)  # the Sales radar never runs
    assert f.state.status is S.RESOLVED and f.state.offers == []
    await after_window(f)
    assert_closed_without_an_offer(f)
    await f.http.aclose()


async def test_no_premium_inventory_for_the_current_flight_closes_after_the_window():
    f = Flow()
    await acknowledged_on_ac1240(f)
    await f.radar.tick()  # Sales finds no inventory on AC1240: proposes nothing
    assert f.state.offers == []
    await after_window(f)
    assert_closed_without_an_offer(f)
    await f.http.aclose()


async def test_no_open_seats_closes_after_the_window():
    f = Flow()
    await f.cancel()
    await f.notify()
    await f.say("Can I leave after 10am?")
    f.rt.world.inventory[0].open_seats = 0  # AC1252 premium is full
    await f.say("Perfect, thanks!")
    assert f.state.offers == []
    await after_window(f)
    assert_closed_without_an_offer(f)
    await f.http.aclose()


async def test_ineligible_tier_the_guardrail_rejects_and_the_journey_closes():
    f = Flow()
    f.rt.world.passengers[PID].loyalty.tier = "basic"
    f.rt.world.passengers[PID].tier = "basic"
    await f.cancel()
    await f.notify()
    await f.say("Can I leave after 10am?")
    await f.say("Perfect, thanks!")
    assert [o.status for o in f.state.offers] == ["rejected"]
    await after_window(f)
    assert_closed_without_an_offer(f)
    await f.http.aclose()


async def test_guardrail_rejection_of_a_bad_offer_still_closes():
    f = Flow()
    await f.cancel()
    await f.notify()
    await f.say("Can I leave after 10am?")
    f.rt.sim.partners  # noqa: B018 (touch)
    await f.rt.handle_inbound(PID, "Perfect, thanks!")  # acknowledged, radar not ticked
    bad = offer_payload(f.state.journey_id, price="289.00", net_margin="234.00")  # no discount
    out = await CustomerSkills(f.rt).propose_and_deliver(bad)
    assert out["status"] == "rejected"
    assert [o.status for o in f.state.offers] == ["rejected"]
    await after_window(f)
    assert_closed_without_an_offer(f)
    await f.http.aclose()


async def test_closure_waits_for_the_window_acknowledgement_and_open_requests():
    f = Flow()
    await f.cancel()
    await f.notify()
    await f.rt.tick()
    f.rt.clock.advance(f.rt.offer_window * 3)
    await f.rt.tick()
    assert f.state.status is S.RESOLVED  # not acknowledged: never closed on a timer alone
    await f.rt.handle_inbound(PID, "Perfect, thanks!")
    f.rt.clock.advance(f.rt.offer_window - timedelta(seconds=30))
    await f.rt.tick()
    assert f.state.status is S.RESOLVED  # the window has not passed yet
    await f.http.aclose()


async def test_a_sent_offer_awaiting_the_passenger_is_not_closed_over():
    f = Flow()
    await f.sent_offer_ready()
    f.rt.clock.advance(f.rt.offer_window * 5)
    await f.rt.tick()
    assert f.state.status is S.RESOLVED and f.state.offers[0].status == "sent"
    await f.http.aclose()


async def test_closing_is_idempotent_and_concurrent_ticks_close_once():
    import asyncio

    f = Flow()
    await acknowledged_on_ac1240(f)
    f.rt.clock.advance(f.rt.offer_window + timedelta(minutes=1))
    await asyncio.gather(f.rt.tick(), f.rt.tick(), f.rt.tick())
    await f.rt.tick()
    t = types(f)
    assert all(t.count(e) == 1 for e in (*CLOSURE_EVENTS, "service_closure"))
    await f.http.aclose()


async def test_an_offer_proposed_after_closure_is_rejected_by_the_guardrail():
    f = Flow()
    await f.cancel()
    await f.notify()
    await f.say("Can I leave after 10am?")
    await f.rt.handle_inbound(PID, "Perfect, thanks!")
    await after_window(f)
    assert f.state.status is S.CLOSED
    with pytest.raises(SkillError, match="disruption_not_resolved"):
        await CustomerSkills(f.rt).propose_and_deliver(offer_payload(f.state.journey_id))
    assert f.state.offers == [] and not f.offer_messages()
    # and Sales no longer sees the journey at all
    assert f.state.journey_id not in CustomerSkills(f.rt).list_journeys()
    await f.http.aclose()


async def test_failed_verification_does_not_close_and_retries_only_after_another_window():
    f = Flow()
    await acknowledged_on_ac1240(f)
    f.rt.world.pnrs["MSLV42"].seats = []  # seat check will fail: seat missing on PNR
    await after_window(f)
    assert f.state.status is S.RESOLVED
    assert types(f).count("verification_failed") == 1
    await f.rt.tick()
    await f.rt.tick()
    assert types(f).count("verification_failed") == 1  # no spam on every poll
    f.rt.clock.advance(f.rt.offer_window + timedelta(minutes=1))
    await f.rt.tick()
    assert types(f).count("verification_failed") == 2  # a bounded retry
    await f.http.aclose()


async def test_the_offer_path_is_unchanged_accept_still_closes_with_an_upgrade():
    from acme.customer.conversion import accept_offer

    f = Flow()
    await f.sent_offer_ready()
    assert await accept_offer(f.rt, f.rec) == "accepted"
    assert f.state.status is S.CLOSED
    assert f.events("analytics_recorded")[0].payload["converted"] is True
    assert not f.events("service_closure")  # the timer path was not involved
    await f.http.aclose()
