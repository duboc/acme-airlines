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


def _needs_human(rec: JourneyRecord, step: str, exc: Exception) -> None:
    rec.state.append_event(ACTOR, "needs_human", {"step": step, "error": str(exc)})


async def book_hotel(rt: CustomerRuntime, rec: JourneyRecord, e: DutyOfCareEntitlements) -> bool:
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
) -> bool:
    legs = [
        ("airport_to_hotel", airport, PARTNER_HOTEL, rt.clock.now() + timedelta(hours=1)),
        ("hotel_to_airport", PARTNER_HOTEL, airport, new_departure - timedelta(hours=3)),
    ]
    for leg, origin, destination, at in legs:
        try:
            r = await rt.client.call(
                "concierge", "transfer.book", origin=origin, destination=destination,
                at=at.isoformat(),
            )  # fmt: skip
        except ToolError as exc:
            _needs_human(rec, f"transfer:{leg}", exc)
            return False
        rec.state.append_event(
            ACTOR, "transfer_arranged",
            {"transfer_ref": r["transfer_ref"], "leg": leg, "entitlement": "ground_transfer",
             "reason": e.rule_reasons["ground_transfer"]},
        )  # fmt: skip
    return True


async def issue_meal_vouchers(
    rt: CustomerRuntime, rec: JourneyRecord, e: DutyOfCareEntitlements
) -> bool:
    ok = True
    for meal in e.meal_vouchers:
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
