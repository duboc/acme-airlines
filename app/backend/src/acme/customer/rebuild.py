"""Scenario 2a: zero-touch journey rebuild (flight, seat, bag) via the specialists."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from acme.customer.specialists.common import RebuildResult
from acme.customer.specialists.baggage import BaggageSpecialist, BagRequest
from acme.customer.specialists.irrops import IrropsRebooking, RebookRequest
from acme.customer.specialists.seat import SeatRequest, SeatSpecialist
from acme.policy.connection_check import check_connection
from acme.sim.client import SimClient

if TYPE_CHECKING:
    from acme.customer.runtime import JourneyRecord


async def rebuild_journey(
    client: SimClient, rec: JourneyRecord, cancelled_flight: str
) -> RebuildResult:
    """Rebook, re-seat and re-route the bag. Leaves the journey RESOLVING either way: a
    failure is a `needs_human` entry, never silent. The result feeds #12's duty of care."""
    state = rec.state

    def emit_as(actor: str):
        return lambda type_, payload: state.append_event(actor, type_, payload)

    pnr = await client.call("pss", "pnr.get", pnr=rec.pnr_locator)
    cancelled = await client.call("pss", "flights.get", flight=cancelled_flight)
    old_seat = next(
        (s["seat"] for s in pnr["seats"] if s["flight_number"] == cancelled_flight), None
    )
    tier = (await client.call("crm", "profile.get", passenger_id=rec.passenger_id))["tier"]
    flights = [s["flight_number"] for s in pnr["segments"]]
    onward = None
    if cancelled_flight in flights and flights.index(cancelled_flight) + 1 < len(flights):
        onward = await client.call(
            "pss", "flights.get", flight=flights[flights.index(cancelled_flight) + 1]
        )
    onward_departure = datetime.fromisoformat(onward["scheduled_departure"]) if onward else None

    rebook = await IrropsRebooking(client).handle(
        RebookRequest(
            pnr=rec.pnr_locator, origin=cancelled["origin"], destination=cancelled["destination"],
            after=datetime.fromisoformat(cancelled["scheduled_departure"]),
            onward_departure=onward_departure,
        ),
        emit_as("irrops_rebooking"),
    )  # fmt: skip
    if rebook.status != "rebooked":
        return RebuildResult(new_flight=None, seat_done=False, bag_done=False)

    seat_done = old_seat is None  # nothing to carry over
    if old_seat:
        seat = await SeatSpecialist(client).handle(
            SeatRequest(
                pnr=rec.pnr_locator, flight=rebook.flight, preferred_seat=old_seat, tier=tier
            ),
            emit_as("seat"),
        )  # fmt: skip
        seat_done = seat.status == "assigned"
    tags = [b["tag"] for b in pnr["bags"]]
    bags = await BaggageSpecialist(client).handle(
        BagRequest(flight=rebook.flight, tags=tags), emit_as("baggage")
    )
    bag_done = set(bags.rerouted) == set(tags)
    if onward and onward_departure and rebook.arrival:
        check = check_connection(onward["origin"], rebook.arrival, onward_departure)
        state.append_event(
            "customer_agent",
            "connection_verified",
            {
                "flight": rebook.flight,
                "connection_flight": onward["number"],
                "feasible": check.feasible,
                "margin_minutes": check.margin_minutes,
            },
        )
    return RebuildResult(new_flight=rebook.flight, seat_done=seat_done, bag_done=bag_done)
