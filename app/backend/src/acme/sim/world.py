"""Shared, async-safe simulated world: the single source of truth for every simulator."""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from acme.domain.clock import Clock, SystemClock
from acme.domain.fixtures import World, mariana_world
from acme.domain.models import PNR, CabinInventory, Flight, Passenger, Segment


class SimError(ValueError):
    """Domain error surfaced to MCP clients as a tool error."""


class SimWorld:
    def __init__(self, world: World, clock: Clock | None = None) -> None:
        self.world = world
        self.clock = clock or SystemClock()
        self.interactions: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._cond = asyncio.Condition()
        from acme.sim.partners import PartnerState

        self.partners = PartnerState(self)

    @classmethod
    def seeded(cls, clock: Clock | None = None) -> "SimWorld":
        return cls(mariana_world(), clock)

    # --- OCC ---------------------------------------------------------------------------
    async def publish_cancellation(self, flight: str, reason: str) -> dict[str, Any]:
        async with self._cond:
            f = self._flight(flight)
            f.status = "cancelled"
            event = {
                "type": "flight_cancelled",
                "flight": flight,
                "reason": reason,
                "at": self.clock.now().isoformat(),
            }
            self._events.append(event)
            self._cond.notify_all()
            return event

    async def events_after(self, cursor: int, timeout_s: float = 0) -> tuple[list[dict], int]:
        """Events with index >= cursor; waits up to `timeout_s` if there are none yet."""
        async with self._cond:
            if len(self._events) <= cursor and timeout_s > 0:
                try:
                    await asyncio.wait_for(
                        self._cond.wait_for(lambda: len(self._events) > cursor), timeout_s
                    )
                except TimeoutError:
                    pass
            return list(self._events[cursor:]), len(self._events)

    # --- PSS ---------------------------------------------------------------------------
    def _flight(self, number: str) -> Flight:
        try:
            return self.world.flights[number]
        except KeyError:
            raise SimError(f"unknown flight {number}") from None

    def get_pnr(self, locator: str) -> PNR:
        try:
            return self.world.pnrs[locator]
        except KeyError:
            raise SimError(f"unknown PNR {locator}") from None

    def search_flights(
        self, origin: str, dest: str, after: datetime | None, before: datetime | None
    ) -> list[Flight]:
        return sorted(
            (
                f
                for f in self.world.flights.values()
                if f.origin == origin
                and f.destination == dest
                and f.status != "cancelled"
                and (after is None or f.scheduled_departure >= after)
                and (before is None or f.scheduled_departure <= before)
            ),
            key=lambda f: f.scheduled_departure,
        )

    async def rebook(self, locator: str, flight: str) -> PNR:
        """Replace the PNR segment on the same route; the old seat is released."""
        async with self._cond:
            pnr, new = self.get_pnr(locator), self._flight(flight)
            for i, seg in enumerate(pnr.segments):
                old = self._flight(seg.flight_number)
                if (old.origin, old.destination) == (new.origin, new.destination):
                    pnr.segments[i] = Segment(flight_number=flight)
                    pnr.seats = [s for s in pnr.seats if s.flight_number != old.number]
                    return pnr
            raise SimError(f"PNR {locator} has no segment on {new.origin}-{new.destination}")

    # --- CRM ---------------------------------------------------------------------------
    def get_passenger(self, passenger_id: str) -> Passenger:
        try:
            return self.world.passengers[passenger_id]
        except KeyError:
            raise SimError(f"unknown passenger {passenger_id}") from None

    def log_interaction(self, passenger_id: str, channel: str, summary: str) -> str:
        self.get_passenger(passenger_id)
        interaction_id = f"I-{uuid4().hex[:8]}"
        self.interactions.append(
            {
                "interaction_id": interaction_id,
                "passenger_id": passenger_id,
                "channel": channel,
                "summary": summary,
                "at": self.clock.now().isoformat(),
            }
        )
        return interaction_id

    # --- Inventory (PII-free) ----------------------------------------------------------
    def cabin_inventory(self, flight: str, cabin: str) -> CabinInventory:
        for inv in self.world.inventory:
            if inv.flight_number == flight and inv.cabin == cabin:
                return inv
        raise SimError(f"no {cabin} inventory for {flight}")

    @staticmethod
    def money(value: Decimal) -> str:
        return f"{value:.2f}"
