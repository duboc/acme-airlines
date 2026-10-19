"""Concierge & Ancillaries specialist: fulfils entitlements through the concierge MCP (#26).

Every booking is recorded on the journey timeline with the entitlement it fulfils and the
policy citation (`reason`). A failed step records `needs_human` and returns False.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from acme.customer.runtime import CustomerRuntime, JourneyRecord
from acme.domain.models import HotelBooking, Voucher
from acme.policy.duty_of_care import DutyOfCareEntitlements
from acme.sim.client import ToolError

PARTNER_HOTEL = "HT8842"
MEAL_VOUCHER_USD = {"breakfast": "12.00", "lunch": "20.00", "dinner": "25.00"}
ACTOR = "concierge_agent"
TRANSFER_LEAD = timedelta(hours=3)  # the hotel pickup is this long before the departure


def _needs_human(rec: JourneyRecord, step: str, exc: Exception) -> None:
    rec.state.append_event(ACTOR, "needs_human", {"step": step, "error": str(exc)})


async def book_hotel(rt: CustomerRuntime, rec: JourneyRecord, e: DutyOfCareEntitlements) -> bool:
    if rec.hotel is not None:
        return True
    try:
        r = await rt.client.call(
            "concierge", "hotel.book", hotel_id=PARTNER_HOTEL, nights=1, pax=1,
            passenger_id=rec.passenger_id,
        )  # fmt: skip
    except ToolError as exc:
        _needs_human(rec, "hotel", exc)
        return False
    rec.hotel = HotelBooking(
        booking_id=r["booking_ref"], hotel_id=r["hotel_id"], passenger_id=rec.passenger_id, nights=1
    )
    rec.state.append_event(
        ACTOR, "hotel_booked",
        {"booking_ref": r["booking_ref"], "hotel_id": r["hotel_id"], "entitlement": "hotel",
         "reason": e.rule_reasons["hotel"]},
    )  # fmt: skip
    return True


async def book_transfers(
    rt: CustomerRuntime,
    rec: JourneyRecord,
    e: DutyOfCareEntitlements,
    airport: str,
    new_departure: datetime,
    with_hotel: bool = True,
) -> bool:
    if with_hotel:
        hotel_id = rec.hotel.hotel_id if rec.hotel else PARTNER_HOTEL  # the booked one (#490)
        legs = [
            ("airport_to_hotel", airport, hotel_id, rt.clock.now() + timedelta(hours=1)),
            ("hotel_to_airport", hotel_id, airport, new_departure - TRANSFER_LEAD),
        ]
    else:  # DOC-06: an origin resident goes home, no hotel
        legs = [("airport_to_home", airport, "HOME", rt.clock.now() + timedelta(hours=1))]
    arranged = {
        ev.payload.get("leg") for ev in rec.state.timeline if ev.type == "transfer_arranged"
    }
    superseded = [
        ev.payload["transfer_ref"]
        for ev in rec.state.timeline
        if ev.type == "transfer_arranged" and ev.payload.get("leg") == "hotel_to_airport"
    ][-1:]
    for leg, origin, destination, at in legs:
        if leg != "hotel_to_airport" and leg in arranged:
            continue
        try:
            r = await rt.client.call(
                "concierge", "transfer.book", origin=origin, destination=destination,
                at=at.isoformat(),
            )  # fmt: skip
            if leg == "hotel_to_airport":
                for ref in superseded:
                    await rt.client.call("concierge", "transfer.cancel", transfer_ref=ref)
        except ToolError as exc:
            _needs_human(rec, f"transfer:{leg}", exc)
            return False
        rec.state.append_event(
            ACTOR, "transfer_arranged",
            {"transfer_ref": r["transfer_ref"], "leg": leg, "entitlement": "ground_transfer",
             "reason": e.rule_reasons["ground_transfer"]},
        )  # fmt: skip
    return True


async def reschedule_pickup(
    rt: CustomerRuntime, rec: JourneyRecord, airport: str, new_departure: datetime
) -> bool:
    """The passenger already has a hotel and a pickup, and a new disruption moved the departure
    without a new transfer entitlement (a same-day wait, DOC-08): book the pickup for the new
    departure and, once that worked, cancel the superseded one. Nothing else is booked (#490)."""
    if rec.hotel is None:
        return True
    superseded = [
        ev.payload["transfer_ref"]
        for ev in rec.state.timeline
        if ev.type == "transfer_arranged" and ev.payload.get("leg") == "hotel_to_airport"
    ][-1:]
    at = new_departure - TRANSFER_LEAD
    try:
        r = await rt.client.call(
            "concierge", "transfer.book", origin=rec.hotel.hotel_id, destination=airport,
            at=at.isoformat(),
        )  # fmt: skip
        for ref in superseded:
            await rt.client.call("concierge", "transfer.cancel", transfer_ref=ref)
    except ToolError as exc:
        _needs_human(rec, "transfer:hotel_to_airport", exc)
        return False
    rec.state.append_event(
        ACTOR, "transfer_arranged",
        {"transfer_ref": r["transfer_ref"], "leg": "hotel_to_airport",
         "entitlement": "ground_transfer", "reason": "rescheduled for the new departure"},
    )  # fmt: skip
    return True


async def issue_meal_vouchers(
    rt: CustomerRuntime, rec: JourneyRecord, e: DutyOfCareEntitlements
) -> bool:
    already = {
        ev.payload.get("meal_type") for ev in rec.state.timeline if ev.type == "voucher_issued"
    }
    ok = True
    for meal in e.meal_vouchers:
        if meal in already:
            continue
        try:
            r = await rt.client.call(
                "concierge", "voucher.issue", type="meal", amount=MEAL_VOUCHER_USD[meal]
            )
        except ToolError as exc:
            _needs_human(rec, f"voucher:{meal}", exc)
            ok = False
            continue
        rec.vouchers.append(
            Voucher(
                voucher_id=r["voucher_id"],
                passenger_id=rec.passenger_id,
                kind="meal",
                amount=Decimal(r["amount"]),
            )  # fmt: skip
        )
        rec.state.append_event(
            ACTOR, "voucher_issued",
            {"voucher_id": r["voucher_id"], "kind": "meal", "meal_type": meal,
             "amount": r["amount"], "entitlement": "meals", "reason": e.rule_reasons["meals"]},
        )  # fmt: skip
    return ok
