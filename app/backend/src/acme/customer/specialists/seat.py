"""Seat specialist: keep the passenger's seat attributes (window) on the new flight."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from acme.customer.specialists.common import Emit
from acme.sim.client import SimClient, ToolError

if TYPE_CHECKING:
    from acme.customer.runtime import JourneyRecord

WINDOW_LETTERS = frozenset("AFK")  # window seats in 3-3 and 3-4-3 layouts (demo simplification)
_SEAT = re.compile(r"^(\d{1,2})([A-K])$")


def nearest_window_seat(open_seats: list[str], wanted: str) -> str | None:
    """The wanted seat if open, else the closest open window seat (same row distance, then
    same letter, then front-most). None if there is no window seat."""
    if wanted in open_seats:
        return wanted
    parsed = _SEAT.match(wanted)
    if parsed is None:  # malformed seat code: caller escalates to a human
        return None
    row, letter = parsed.groups()
    candidates = [s for s in open_seats if (m := _SEAT.match(s)) and m.group(2) in WINDOW_LETTERS]
    if not candidates:
        return None

    def key(seat: str) -> tuple[int, bool, int]:
        r, letr = _SEAT.match(seat).groups()  # type: ignore[union-attr]
        return abs(int(r) - int(row)), letr != letter, int(r)

    return min(candidates, key=key)


def preferred_seat(rec: JourneyRecord, on_flight: str | None) -> str | None:
    """The seat the passenger WANTS kept: the one they held before the first disruption (the
    first `profile_loaded` seat, else the first `seat_held` request), not whatever stands in
    the PNR now, which may be a courtesy substitute or a voided upgrade (#491). `on_flight` is
    the seat on the flight being replaced: the last resort, for a journey with no history."""
    timeline = rec.state.timeline
    first = next(
        (
            e.payload["seat"]
            for e in timeline
            if e.type == "profile_loaded" and e.payload.get("seat")
        ),
        None,
    )
    first = first or next(
        (
            e.payload["requested"]
            for e in timeline
            if e.type == "seat_held" and e.payload.get("requested")
        ),
        None,
    )
    return first or on_flight


class SeatRequest(BaseModel):
    pnr: str
    flight: str
    preferred_seat: str
    tier: str


class SeatResponse(BaseModel):
    status: Literal["assigned", "unavailable"]
    seat: str | None = None
    reason: str | None = None


class SeatSpecialist:
    def __init__(self, client: SimClient) -> None:
        self._client = client

    async def handle(self, req: SeatRequest, emit: Emit) -> SeatResponse:
        try:
            return await self._assign(req, emit)
        except ToolError as exc:
            emit("needs_human", {"specialist": "seat", "reason": str(exc)[:200]})
            return SeatResponse(status="unavailable")

    async def _assign(self, req: SeatRequest, emit: Emit) -> SeatResponse:
        seatmap = await self._client.call("dcs", "seatmap.get", flight=req.flight)
        seat = nearest_window_seat(seatmap["seats"].get("economy", []), req.preferred_seat)
        if seat is None:
            emit(
                "needs_human",
                {
                    "specialist": "seat",
                    "reason": f"{req.preferred_seat} unavailable on {req.flight} and no window "
                    "seat is open",
                },
            )
            return SeatResponse(status="unavailable")
        await self._client.call("dcs", "seat.hold", flight=req.flight, seat=seat, pnr=req.pnr)
        await self._client.call(
            "dcs", "seat.assign", flight=req.flight, seat=seat, pnr=req.pnr, cabin="economy"
        )
        if seat == req.preferred_seat:
            reason = f"kept seat {seat} on {req.flight}"
        else:
            reason = (
                f"{req.preferred_seat} unavailable on {req.flight}; assigned nearest window "
                f"seat {seat} as a {req.tier} tier courtesy"
            )
        emit(
            "seat_held",
            {"flight": req.flight, "seat": seat, "requested": req.preferred_seat, "reason": reason},
        )
        return SeatResponse(status="assigned", seat=seat, reason=reason)
