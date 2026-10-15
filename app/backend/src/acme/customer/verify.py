"""Closed-loop verification: do PSS, DCS, concierge, WorldTracer and payments agree?

Expectations come from the journey's own timeline (what the Customer agent did); actuals come from
the systems (PSS PNR, WorldTracer bag routing via MCP; hotel bookings and charges from the
in-process simulator state, which the Customer runtime owns). Any mismatch is a failed check with
a human-readable detail; the caller records `verification_failed` and does not close the journey.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acme.customer.runtime import CustomerRuntime, JourneyRecord

CHECK_NAMES = ("flight", "seat", "hotel", "baggage", "upgrade")


@dataclass
class VerificationResult:
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    @property
    def failed(self) -> list[str]:
        return [name for name, passed in self.checks.items() if not passed]


def _last(rec: JourneyRecord, type_: str) -> dict[str, Any] | None:
    found = [e.payload for e in rec.state.timeline if e.type == type_]
    return found[-1] if found else None


async def verify_journey(rt: CustomerRuntime, rec: JourneyRecord) -> VerificationResult:
    res = VerificationResult()

    def check(name: str, passed: bool, detail: str = "") -> None:
        res.checks[name] = passed
        if not passed:
            res.details[name] = detail

    pnr = await rt.client.call("pss", "pnr.get", pnr=rec.pnr_locator)
    rebooked = _last(rec, "rebooked")
    flight = rebooked["flight"] if rebooked else None
    segments = [s["flight_number"] for s in pnr["segments"]]

    # flight (PSS)
    status = None
    if flight in segments:
        status = (await rt.client.call("pss", "flights.get", flight=flight))["status"]
    check(
        "flight",
        flight is not None and flight in segments and status != "cancelled",
        f"expected {flight} on the PNR, found segments {segments}",
    )

    # seat (DCS view on the PNR)
    accepted = [r for r in rec.state.offers if r.status == "accepted"]
    upgrade = _last(rec, "upgrade_confirmed") if accepted else None
    held = _last(rec, "seat_held")
    expect = (
        (upgrade["seat"], upgrade["cabin"])
        if upgrade
        else ((held["seat"], None) if held else (None, None))
    )
    seats = [s for s in pnr["seats"] if s["flight_number"] == flight]
    check(
        "seat",
        len(seats) == 1
        and seats[0]["seat"] == expect[0]
        and (expect[1] is None or seats[0]["cabin"] == expect[1]),
        f"expected exactly one seat {expect[0]} on {flight}, found {[s['seat'] for s in seats]}",
    )

    # hotel (concierge)
    hotel = _last(rec, "hotel_booked")
    booking = rt.sim.partners.hotel_bookings.get(hotel["booking_ref"]) if hotel else None
    assessed = _last(rec, "duty_of_care_assessed")
    if hotel is None and assessed is not None and not assessed["hotel_required"]:
        hotel_ok = rec.hotel is None  # DOC-06 / DOC-08: no hotel was due, none was booked
    else:
        hotel_ok = bool(booking and rec.hotel and rec.hotel.booking_id == hotel["booking_ref"])
    check("hotel", hotel_ok, "hotel booking missing in the concierge system or on the journey")

    # baggage (WorldTracer)
    routings = []
    for bag in pnr["bags"]:
        routings.append((await rt.client.call("worldtracer", "bag.get", tag=bag["tag"]))["routing"])
    check(
        "baggage",
        bool(routings) and all(r == flight for r in routings),
        f"expected bags routed to {flight}, found {routings}",
    )

    # upgrade / payment
    net = rt.sim.partners.net_charged(rec.pnr_locator)
    if accepted:
        record = accepted[-1]
        charge = rt.sim.partners.charges.get(str(record.offer.offer_id))
        price = Decimal(record.offer.price)
        good = bool(charge and charge["status"] == "succeeded" and net == price)
        good = good and upgrade is not None
        check("upgrade", good, f"expected exactly one successful charge of {price}, net is {net}")
    else:
        premium = [s for s in pnr["seats"] if s["cabin"] != "economy"]
        check("upgrade", net == 0 and not premium, f"no upgrade expected; net charge {net}")
    return res
