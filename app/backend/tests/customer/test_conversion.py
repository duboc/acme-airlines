"""Scenario 5a: one-tap conversion with the acceptance-time guardrail re-check (#28)."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from acme.api.events import TimelineEvent
from acme.customer.conversion import NoSentOffer, accept_offer, decline_offer
from acme.customer.runtime import CustomerRuntime
from acme.customer.skills import CustomerSkills
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus as S
from acme.sim.client import ToolError
from acme.sim.dcs import dcs_state

PNR = "MSLV42"


def offer(jid):
    return dict(
        offer_id=str(uuid4()), journey_id=jid, flight="AC1252", cabin="premium_business",
        price="135.00", standard_price="290.00", net_margin="80.00", currency="USD",
        origin="sales", optional=True,
    )  # fmt: skip


@pytest.fixture
async def c():
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), auto_duty_of_care=False)
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    await rt.sim.rebook(PNR, "AC1252")
    dcs_state(rt.sim).assign("AC1252", "14A", PNR, "economy")  # her kept seat on the new flight
    rec = rt.journeys[jid]
    rec.state.transition(S.RESOLVED)
    rec.state.resolution_acknowledged = True
    payload = offer(jid)
    out = await CustomerSkills(rt).propose_and_deliver(payload)
    assert out["status"] == "approved" and rec.state.offers[0].status == "sent"
    return type("C", (), {"rt": rt, "rec": rec, "state": rec.state, "payload": payload})


def seat_of(c):
    return next((s for s in c.rt.world.pnrs[PNR].seats if s.flight_number == "AC1252"), None)


def last_text(c):
    return c.rt.sim.partners.transcripts["P-MARIANA"][-1]["body"]


def sent_count(c):
    return len(c.rt.sim.partners.transcripts["P-MARIANA"])


def charges(c):
    return c.rt.sim.partners.charges


async def test_accept_charges_once_assigns_2a_issues_boarding_pass(c):
    out = await accept_offer(c.rt, c.rec)
    assert out == "accepted"
    (charge,) = charges(c).values()
    assert charge["amount"] == "135.00" and charge["status"] == "succeeded"
    assert list(charges(c)) == [c.payload["offer_id"]]  # idempotency key = offer id
    seat = seat_of(c)
    assert (seat.seat, seat.cabin) == ("2A", "premium_business")
    (bp,) = [b for b in dcs_state(c.rt.sim).boarding_passes.values() if b["flight"] == "AC1252"]
    assert (bp["seat"], bp["cabin"]) == ("2A", "premium_business")
    assert c.state.offers[0].status == "accepted"
    assert "2A" in last_text(c)
    assert any(e.type == "upgrade_confirmed" for e in c.state.timeline)


async def test_duplicate_and_concurrent_accepts_charge_once(c):
    results = await asyncio.gather(*[accept_offer(c.rt, c.rec) for _ in range(4)])
    assert set(results) == {"accepted"}
    assert len(charges(c)) == 1 and c.rt.sim.partners.net_charged(PNR) == Decimal("135.00")
    assert len([b for b in dcs_state(c.rt.sim).boarding_passes.values()]) == 1
    assert await accept_offer(c.rt, c.rec) == "accepted"  # a later tap too
    assert len(charges(c)) == 1


async def _assert_refused(c, before_msgs, seat="14A"):
    assert charges(c) == {} and (seat_of(c).seat if seat_of(c) else None) == seat
    assert c.state.offers[0].status == "invalidated"
    assert sent_count(c) == before_msgs + 1
    assert "no longer available" in last_text(c)


async def test_accept_after_replanning_is_refused(c):
    c.state.transition(S.REPLANNING)  # also invalidates the live offer
    before = sent_count(c)
    assert await accept_offer(c.rt, c.rec) == "invalidated"
    await _assert_refused(c, before)


async def test_accept_after_journey_moved_to_another_flight_is_refused(c):
    await c.rt.sim.rebook(PNR, "AC1260")  # releases the AC1252 seat; no premium seat is touched
    before = sent_count(c)
    assert await accept_offer(c.rt, c.rec) == "invalidated"
    await _assert_refused(c, before, seat=None)


async def test_accept_when_premium_inventory_is_zero_is_refused(c):
    c.rt.world.inventory[0].open_seats = 0
    before = sent_count(c)
    assert await accept_offer(c.rt, c.rec) == "invalidated"
    await _assert_refused(c, before)


async def test_accept_when_2a_is_taken_by_another_pnr_never_charges(c):
    from acme.domain.models import PNR as Pnr
    from acme.domain.models import Segment

    c.rt.world.pnrs["OTHER1"] = Pnr(
        locator="OTHER1", passenger_id="P-MARIANA", segments=[Segment(flight_number="AC1252")]
    )
    dcs_state(c.rt.sim).hold("AC1252", "2A", "OTHER1")
    before = sent_count(c)
    assert await accept_offer(c.rt, c.rec) == "invalidated"
    await _assert_refused(c, before)


async def test_seat_assign_failure_after_charge_voids_the_charge(c, monkeypatch):
    real = c.rt.client.call

    async def call(server, tool, **args):
        if tool == "seat.assign":
            raise ToolError("dcs/seat.assign: boom")
        return await real(server, tool, **args)

    monkeypatch.setattr(c.rt.client, "call", call)
    before = sent_count(c)
    assert await accept_offer(c.rt, c.rec) == "invalidated"
    (charge,) = charges(c).values()
    assert charge["status"] == "voided" and c.rt.sim.partners.net_charged(PNR) == 0
    assert seat_of(c).seat == "14A" and c.state.offers[0].status == "invalidated"
    assert sent_count(c) == before + 1
    assert "not been charged" in last_text(c) or "reversed" in last_text(c)
    assert any(e.type == "charge_voided" for e in c.state.timeline)


async def test_charge_failure_leaves_no_charge_and_keeps_14a(c, monkeypatch):
    real = c.rt.client.call

    async def call(server, tool, **args):
        if tool == "charge":
            raise ToolError("payments/charge: declined")
        return await real(server, tool, **args)

    monkeypatch.setattr(c.rt.client, "call", call)
    assert await accept_offer(c.rt, c.rec) == "invalidated"
    assert charges(c) == {} and seat_of(c).seat == "14A"


async def test_hold_precedes_charge(c, monkeypatch):
    order = []
    real = c.rt.client.call

    async def call(server, tool, **args):
        if tool in ("seat.hold", "charge", "seat.assign", "boarding_pass.issue", "charge.void"):
            order.append(tool)
        return await real(server, tool, **args)

    monkeypatch.setattr(c.rt.client, "call", call)
    await accept_offer(c.rt, c.rec)
    assert order == ["seat.hold", "charge", "seat.assign", "boarding_pass.issue"]


async def test_void_failure_is_escalated_to_a_human(c, monkeypatch):
    real = c.rt.client.call

    async def call(server, tool, **args):
        if tool in ("seat.assign", "charge.void"):
            raise ToolError("down")
        return await real(server, tool, **args)

    monkeypatch.setattr(c.rt.client, "call", call)
    await accept_offer(c.rt, c.rec)
    assert any(e.type == "needs_human" for e in c.state.timeline)


async def test_no_thanks_declines_silently_and_blocks_further_offers(c):
    before = sent_count(c)
    assert await decline_offer(c.rt, c.rec) == "declined"
    assert charges(c) == {} and seat_of(c).seat == "14A" and sent_count(c) == before
    again = await CustomerSkills(c.rt).propose_and_deliver(offer(c.rec.state.journey_id))
    assert "offer_already_made" in again["reasons"] and sent_count(c) == before


async def test_accept_or_decline_without_a_sent_offer_is_an_error(c):
    c.state.offers[0].status = "declined"
    with pytest.raises(NoSentOffer):
        await accept_offer(c.rt, c.rec)
    with pytest.raises(NoSentOffer):
        await decline_offer(c.rt, c.rec)


async def test_events_conform_to_the_typed_union(c, monkeypatch):
    await accept_offer(c.rt, c.rec)
    adapter = TypeAdapter(TimelineEvent)
    for e in c.state.timeline:
        adapter.validate_python(e.model_dump(mode="json"))


async def test_api_action_endpoint_converts_and_reflects_state():
    import httpx

    from acme.api.app import create_app

    app = create_app(FixedClock(DISRUPTION_DETECTED_AT))
    rt = app.state.runtime
    rt.auto_duty_of_care = False
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    await rt.sim.rebook(PNR, "AC1252")
    dcs_state(rt.sim).assign("AC1252", "14A", PNR, "economy")
    rec = rt.journeys[jid]
    rec.state.transition(S.RESOLVED)
    rec.state.resolution_acknowledged = True
    await CustomerSkills(rt).propose_and_deliver(offer(jid))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as h:
        r = await h.post("/whatsapp/P-MARIANA/actions", json={"action": "accept_offer"})
        r2 = await h.post("/whatsapp/P-MARIANA/actions", json={"action": "accept_offer"})
        d = (await h.get(f"/journeys/{jid}")).json()
    assert r.json() == {"offer_status": "accepted"} and r2.json() == {"offer_status": "accepted"}
    assert d["offer"]["status"] == "accepted" and d["seat"]["seat"] == "2A"
    assert len(rt.sim.partners.charges) == 1
