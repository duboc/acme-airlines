"""Scenario 2b: duty of care (Rights -> Concierge) and the RESOLVED transition.

`run_duty_of_care` is called once the rebuild (#11: flight, seat, bag) has run, with its
`RebuildResult`. The journey becomes RESOLVED only if the rebuild is complete AND every
duty-of-care step succeeded; any failed step leaves it RESOLVING with a `needs_human` entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from acme.customer.runtime import CustomerRuntime, JourneyRecord
from acme.customer.specialists import concierge, rights
from acme.domain.journey import DisruptionStatus


@dataclass(frozen=True)
class RebuildResult:
    new_flight: str | None
    seat_done: bool
    bag_done: bool


def _missing(rebuild: RebuildResult) -> list[str]:
    return [
        name
        for name, done in (
            ("flight", rebuild.new_flight is not None),
            ("seat", rebuild.seat_done),
            ("bag", rebuild.bag_done),
        )
        if not done
    ]


async def run_duty_of_care(rt: CustomerRuntime, rec: JourneyRecord, rebuild: RebuildResult) -> None:
    state = rec.state
    if state.status is DisruptionStatus.RESOLVED:
        return
    if missing := _missing(rebuild):
        state.append_event("customer_agent", "resolution_blocked", {"missing": missing})
        return

    detected = state.timeline[0]
    original = await rt.client.call("pss", "flights.get", flight=detected.payload["flight"])
    new = await rt.client.call("pss", "flights.get", flight=rebuild.new_flight)
    new_departure = datetime.fromisoformat(new["scheduled_departure"])
    e = rights.assess(
        origin=original["origin"],
        destination=original["destination"],
        reason=detected.payload["reason"],
        detected_at=detected.timestamp,
        original_departure=datetime.fromisoformat(original["scheduled_departure"]),
        new_departure=new_departure,
    )
    state.append_event("rights_agent", "duty_of_care_assessed", rights.entitlements_payload(e))

    ok = True
    if e.hotel_required:
        hotel_ok = await concierge.book_hotel(rt, rec, e)
        ok &= hotel_ok
        if hotel_ok and e.ground_transfer_required:
            ok &= await concierge.book_transfers(rt, rec, e, original["origin"], new_departure)
    if e.meal_vouchers:
        ok &= await concierge.issue_meal_vouchers(rt, rec, e)
    if not ok:
        return
    state.append_event("customer_agent", "duty_of_care_fulfilled", {})
    state.transition(DisruptionStatus.RESOLVED, actor="customer_agent")
