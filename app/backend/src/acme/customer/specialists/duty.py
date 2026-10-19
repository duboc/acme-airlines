"""Scenario 2b: duty of care (Rights -> Concierge) and the RESOLVED transition.

`run_duty_of_care` is called once the rebuild (#11: flight, seat, bag) has run, with its
`RebuildResult`. The journey becomes RESOLVED only if the rebuild is complete AND every
duty-of-care step succeeded; any failed step leaves it RESOLVING with a `needs_human` entry.
"""

from __future__ import annotations

from datetime import datetime

from acme.customer.runtime import CustomerRuntime, JourneyRecord
from acme.customer.specialists import concierge, rights
from acme.customer.specialists.common import RebuildResult
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus
from acme.telemetry import specialist_span

SEEDED_PRIMARY_FLIGHT = "AC1234"  # the cancellation the demo world is seeded around


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

    detected = next(
        (e for e in reversed(state.timeline) if e.type == "cancellation_detected"),
        state.timeline[0],
    )
    original = await rt.client.call("pss", "flights.get", flight=detected.payload["flight"])
    new = await rt.client.call("pss", "flights.get", flight=rebuild.new_flight)
    new_departure = datetime.fromisoformat(new["scheduled_departure"])
    original_departure = datetime.fromisoformat(original["scheduled_departure"])
    detected_at = detected.timestamp
    if detected.payload["flight"] == SEEDED_PRIMARY_FLIGHT and detected_at > original_departure:
        # A manual trigger of the seeded cancellation on the live clock lands days after the
        # seeded schedule: the wait still begins when the demo's disruption was detected (#494).
        detected_at = DISRUPTION_DETECTED_AT
    wait_begin = min(detected_at, original_departure).astimezone(original_departure.tzinfo)
    profile = await rt.client.call("crm", "profile.get", passenger_id=rec.passenger_id)
    with specialist_span("rights", state.journey_id):
        e = rights.assess(
            origin=original["origin"],
            destination=original["destination"],
            reason=detected.payload["reason"],
            detected_at=detected_at,
            original_departure=original_departure,
            new_departure=new_departure,
            resident=bool(profile.get("origin_resident", False)),
            special_needs=bool(profile.get("special_needs", False)),
        )
        state.append_event("rights_agent", "duty_of_care_assessed", rights.entitlements_payload(e))

    ok = True
    with specialist_span("concierge", state.journey_id):
        hotel_ok = True
        if e.hotel_required:
            hotel_ok = await concierge.book_hotel(rt, rec, e)
            ok &= hotel_ok
        if hotel_ok and e.ground_transfer_required:  # DOC-06 has a transfer and no hotel
            ok &= await concierge.book_transfers(
                rt,
                rec,
                e,
                original["origin"],
                new_departure,
                with_hotel=e.hotel_required,
                wait_begin=wait_begin,
            )
        elif hotel_ok and rec.hotel is not None:  # DOC-08 again, with the hotel already booked
            ok &= await concierge.reschedule_pickup(rt, rec, original["origin"], new_departure)
        if e.meal_vouchers:
            ok &= await concierge.issue_meal_vouchers(rt, rec, e)
    if not ok:
        return
    state.append_event("customer_agent", "duty_of_care_fulfilled", {})
    state.transition(DisruptionStatus.RESOLVED, actor="customer_agent")
