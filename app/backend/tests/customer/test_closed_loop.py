"""Scenario 5b: closed-loop verification, journey memory, CRM/NPS/analytics, CLOSED (#16)."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from acme.api.events import TimelineEvent
from acme.customer.conversion import accept_offer, decline_offer
from acme.customer.runtime import CustomerRuntime
from acme.customer.skills import CustomerSkills
from acme.customer.verify import verify_journey
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus as S
from acme.domain.models import SeatAssignment
from acme.sim.dcs import dcs_state

PNR = "MSLV42"
CHECKS = {"flight", "seat", "hotel", "baggage", "upgrade"}


@pytest.fixture
async def c():
    """The brief's path: cancel -> notified -> replan (after 10:00 -> AC1252) -> acknowledged."""
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT))
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    rt.clock.advance(timedelta(minutes=7))
    await rt.tick()  # the one proactive notification
    await rt.handle_inbound("P-MARIANA", "Can I leave after 10:00 AM instead?")
    await rt.handle_inbound("P-MARIANA", "Thanks, that all works for me!")
    rec = rt.journeys[jid]
    assert rec.state.status == S.RESOLVED and rec.state.resolution_acknowledged
    return type("C", (), {"rt": rt, "rec": rec, "state": rec.state, "jid": jid})


def offer(jid, flight="AC1252"):
    return dict(
        offer_id=str(uuid4()), journey_id=jid, flight=flight, cabin="premium_business",
        price="135.00", standard_price="290.00", net_margin="80.00", currency="USD",
        origin="sales", optional=True,
    )  # fmt: skip


async def sent_offer(c):
    out = await CustomerSkills(c.rt).propose_and_deliver(offer(c.jid))
    assert out["status"] == "approved", out
    return "AC1252"


def events(c, type_):
    return [e for e in c.state.timeline if e.type == type_]


async def test_accept_runs_the_closed_loop_and_closes_the_journey(c):
    await sent_offer(c)
    assert await accept_offer(c.rt, c.rec) == "accepted"
    assert c.state.status == S.CLOSED
    (passed,) = events(c, "verification_passed")
    assert set(passed.payload["checks"]) == CHECKS and all(passed.payload["checks"].values())
    assert len(events(c, "memory_committed")) == 1
    assert len(events(c, "crm_logged")) == 1 and len(events(c, "nps_scheduled")) == 1
    (a,) = events(c, "analytics_recorded")
    assert a.payload["converted"] is True and Decimal(a.payload["margin"]) == Decimal("80")
    assert a.payload["event"] == "offer_accepted"


async def test_persisted_and_reloadable_with_an_identical_timeline(c):
    await sent_offer(c)
    await accept_offer(c.rt, c.rec)
    back = c.rt.store.load(c.jid, c.rt.clock)
    assert back.timeline == c.state.timeline and back.status == S.CLOSED
    assert [r.status for r in back.offers] == ["accepted"]


async def test_crm_interaction_and_nps_scheduled_after_the_trip_date(c):
    await sent_offer(c)
    await accept_offer(c.rt, c.rec)
    logs = c.rt.sim.interactions
    assert len(logs) == 1 and logs[0]["passenger_id"] == "P-MARIANA"
    (survey,) = c.rt.sim.partners.surveys
    last = c.rt.world.flights[c.rt.world.pnrs[PNR].segments[-1].flight_number]
    trip_end = last.scheduled_arrival or last.scheduled_departure
    from datetime import datetime

    assert datetime.fromisoformat(survey["send_after"]) > trip_end + timedelta(0)


async def test_tampered_dcs_seat_fails_verification_and_does_not_close(c):
    await sent_offer(c)
    await accept_offer(c.rt, c.rec)  # closes; replay verification on a tampered world
    flight = "AC1252"
    c.rt.world.pnrs[PNR].seats = [SeatAssignment(flight_number=flight, seat="3C", cabin="economy")]
    result = await verify_journey(c.rt, c.rec)
    assert result.checks["seat"] is False and not result.ok


async def test_tamper_before_close_leaves_the_journey_open_with_verification_failed(c):
    flight = await sent_offer(c)
    dcs = dcs_state(c.rt.sim)
    real = dcs.boarding_pass

    def tamper(*a, **k):
        out = real(*a, **k)
        c.rt.world.pnrs[PNR].seats = [
            SeatAssignment(flight_number=flight, seat="3C", cabin="economy")
        ]
        return out

    dcs.boarding_pass = tamper  # the DCS seat drifts right after the upgrade is issued
    await accept_offer(c.rt, c.rec)
    assert c.state.status == S.RESOLVED
    (failed,) = events(c, "verification_failed")
    assert "seat" in failed.payload["failed"]
    assert not events(c, "verification_passed") and not events(c, "memory_committed")
    assert c.rt.sim.partners.surveys == [] and c.rt.sim.interactions == []


async def test_no_verification_before_the_passenger_decides(c):
    await sent_offer(c)
    assert events(c, "verification_passed") == [] and c.state.status == S.RESOLVED


async def test_decline_also_closes_with_converted_false(c):
    await sent_offer(c)
    assert await decline_offer(c.rt, c.rec) == "declined"
    assert c.state.status == S.CLOSED
    (a,) = events(c, "analytics_recorded")
    assert a.payload["converted"] is False and a.payload["event"] == "offer_declined"
    checks = events(c, "verification_passed")[0].payload["checks"]
    assert checks["upgrade"] is True  # no upgrade and no net charge is consistent


async def test_declined_journey_with_a_stray_charge_fails_the_upgrade_check(c):
    await sent_offer(c)
    c.rt.sim.partners.charge(PNR, Decimal("135"), "USD", "stray")
    await decline_offer(c.rt, c.rec)
    assert c.state.status == S.RESOLVED
    assert "upgrade" in events(c, "verification_failed")[0].payload["failed"]


async def test_all_events_conform_to_the_typed_union(c):
    await sent_offer(c)
    await accept_offer(c.rt, c.rec)
    adapter = TypeAdapter(TimelineEvent)
    for e in c.state.timeline:
        adapter.validate_python(e.model_dump(mode="json"))


async def test_api_shows_closed_with_verification_and_sse_emits_it():
    import httpx

    from acme.api.app import create_app

    app = create_app(FixedClock(DISRUPTION_DETECTED_AT))
    rt = app.state.runtime
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    rt.clock.advance(timedelta(minutes=7))
    await rt.tick()
    await rt.handle_inbound("P-MARIANA", "Can I leave after 10:00 AM instead?")
    await rt.handle_inbound("P-MARIANA", "Thanks, that all works for me!")
    rec = rt.journeys[jid]
    holder = type("C", (), {"rt": rt, "rec": rec, "state": rec.state, "jid": jid})
    await sent_offer(holder)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as h:
        r = await h.post("/whatsapp/P-MARIANA/actions", json={"action": "accept_offer"})
        d = (await h.get(f"/journeys/{jid}")).json()
        n = len(rec.state.timeline)
        sse = await h.get(f"/journeys/{jid}/events?limit={n}")
    assert r.json() == {"offer_status": "accepted"}
    assert d["status"] == "CLOSED" and set(d["verification"]) == CHECKS
    assert "verification_passed" in sse.text and "analytics_recorded" in sse.text
