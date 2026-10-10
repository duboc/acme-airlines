"""In-memory runtime state behind the API. Agent behaviour is STUBBED here (#17).

The Customer agent and friends (#10-#16) replace the stubbed reactions in `api/app.py`
as they land; the HTTP contract stays the same.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel

from acme.domain.clock import Clock, SystemClock
from acme.domain.fixtures import World, mariana_world
from acme.domain.journey import JourneyState
from acme.domain.models import HotelBooking, Voucher


class WhatsAppMessage(BaseModel):
    message_id: str
    direction: Literal["inbound", "outbound"]
    text: str
    buttons: list[str] = []
    timestamp: AwareDatetime


@dataclass
class JourneyRecord:
    state: JourneyState
    passenger_id: str
    pnr_locator: str
    hotel: HotelBooking | None = None
    vouchers: list[Voucher] = field(default_factory=list)


def journey_id_for(locator: str) -> str:
    return "J-" + hashlib.sha1(locator.encode()).hexdigest()[:8]  # noqa: S324 (not security)


class Runtime:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()
        self.reset()

    def reset(self) -> None:
        self.world: World = mariana_world()
        self.journeys: dict[str, JourneyRecord] = {}
        self.messages: dict[str, list[WhatsAppMessage]] = {}

    def say(self, passenger_id: str, direction: str, text: str, buttons=()) -> WhatsAppMessage:
        msg = WhatsAppMessage(
            message_id=str(uuid4()),
            direction=direction,
            text=text,
            buttons=list(buttons),
            timestamp=self.clock.now(),
        )
        self.messages.setdefault(passenger_id, []).append(msg)
        return msg

    def journey_for_passenger(self, passenger_id: str) -> JourneyRecord | None:
        for rec in self.journeys.values():
            if rec.passenger_id == passenger_id:
                return rec
        return None

    def cancel_flight(self, flight: str, reason: str) -> list[str]:
        """Mark the flight cancelled and open a journey per affected PNR. Raises KeyError."""
        self.world.flights[flight].status = "cancelled"
        ids = []
        for pnr in self.world.pnrs.values():
            if flight not in {s.flight_number for s in pnr.segments}:
                continue
            jid = journey_id_for(pnr.locator)
            passenger = self.world.passengers[pnr.passenger_id]
            if jid not in self.journeys:
                state = JourneyState(jid, self.clock)
                self.journeys[jid] = JourneyRecord(state, pnr.passenger_id, pnr.locator)
                state.append_event(
                    "occ", "cancellation_detected", {"flight": flight, "reason": reason}
                )
                self.say(
                    pnr.passenger_id,
                    "outbound",
                    f"Hi {passenger.name}, flight {flight} was cancelled ({reason}). "
                    "We are already working on your rebooking.",
                )
            ids.append(jid)
        return ids
