"""Margin radar: finds journeys the Customer agent reports as eligible and proposes ONE offer.

Sales sees only what the Customer skills expose (status, flight, acknowledged) and
inventory/fares from the `inventory` MCP server. It never talks to a passenger and writes
nothing to journeys; suppression is recorded by the Customer side when it answers
`journey_status`. The Customer guardrail (rule 5 `offer_already_made`) is the authoritative
backstop for duplicates; the sent-set here only avoids sending them. It is keyed by the
journey's opaque `run_id` so a scenario reset (same deterministic journey id) starts afresh.

Pricing: Sales is PII-free, so it does not know the passenger's tier. It prices with the best
courtesy tier (`COURTESY_TIER`); the guardrail re-checks the price against the real tier cap.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from acme.domain.models import CabinInventory, Offer, Tier
from acme.policy.margin_opt import recovery_quote
from acme.sales.client import CustomerPort
from acme.sim.client import SimClient, ToolError
from acme.telemetry import span

CABIN = "premium_business"
COURTESY_TIER: Tier = "diamond"


class SalesRadar:
    def __init__(self, customer: CustomerPort, inventory: SimClient) -> None:
        self._customer = customer
        self._inventory = inventory
        self._sent: set[tuple[str, str, str]] = set()  # (journey_id, run_id, flight)

    def export_sent(self) -> list[list[str]]:
        """The sent-set as JSON rows (shared across Agent Engine processes, #200)."""
        return [list(key) for key in sorted(self._sent)]

    def import_sent(self, rows: list[list[str]]) -> None:
        self._sent = {(j, r, f) for j, r, f in rows}

    async def tick(self) -> list[Offer]:
        """One radar pass. Returns the offers proposed in this pass."""
        proposed: list[Offer] = []
        for journey_id in await self._customer.list_journeys():
            status = await self._customer.journey_status(journey_id)
            flight = status["flight"]
            if (
                status["status"] != "RESOLVED"
                or not status["resolution_acknowledged"]
                or not flight
            ):
                continue  # suppressed: emit nothing
            key = (journey_id, status["run_id"], flight)
            if key in self._sent:
                continue
            offer = await self._price(journey_id, flight)
            if offer is None:
                continue
            with span("acme.sales.pitch", acme__journey_id=journey_id, acme__flight=flight):
                await self._customer.propose_offer(offer)
            # Recorded once the Customer answered (a guardrail rejection is an answer): a transient
            # RPC error leaves the key out so the next tick retries (#392).
            self._sent.add(key)
            proposed.append(offer)
        return proposed

    async def _price(self, journey_id: str, flight: str) -> Offer | None:
        try:
            seats = await self._inventory.call(
                "inventory", "cabin_inventory.get", flight=flight, cabin=CABIN
            )
            fare = await self._inventory.call(
                "inventory", "upgrade_fare.get", flight=flight, cabin=CABIN
            )
        except ToolError:
            return None  # no premium inventory on this flight
        if seats["open_seats"] <= 0:
            return None
        inventory = CabinInventory(
            flight_number=flight,
            cabin=CABIN,
            open_seats=seats["open_seats"],
            standard_upgrade_fare=Decimal(fare["standard_fare"]),
            cost_floor=Decimal(fare["cost_floor"]),
        )
        quote = recovery_quote(inventory, COURTESY_TIER)
        if quote is None:
            return None
        return Offer(
            offer_id=uuid4(),
            journey_id=journey_id,
            flight=flight,
            cabin=CABIN,
            price=quote.price,
            standard_price=quote.standard_price,
            net_margin=quote.net_margin,
            currency="USD",
            origin="sales",
            optional=True,
        )
