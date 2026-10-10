"""Real `ScenarioDriver` (see tests/scenario/driver.py): synchronous facade over the runtime.

Deterministic: fixed clock, in-process MCP simulators, no network, no LLM.
"""

import asyncio
from datetime import datetime

from acme.customer.conversion import accept_offer, decline_offer
from acme.customer.runtime import CustomerRuntime
from acme.customer.skills import CustomerSkills
from acme.domain.clock import FixedClock
from acme.domain.fixtures import mariana_world
from acme.domain.journey import JourneyState
from acme.domain.models import Offer
from acme.sales.radar import SalesRadar
from acme.sim.client import SimClient, inprocess_connector

_PASSENGER_ID = next(iter(mariana_world().passengers))
# The runtime sends human-readable button labels; the harness speaks stable button ids.
_BUTTON_IDS = {"Accept": "accept_upgrade", "No thanks": "decline_upgrade"}


class _InProcessCustomer:
    """`CustomerPort` for the Sales radar, calling the Customer skills directly (no HTTP)."""

    def __init__(self, skills: CustomerSkills) -> None:
        self._skills = skills

    async def list_journeys(self) -> list[str]:
        return self._skills.list_journeys()

    async def journey_status(self, journey_id: str) -> dict:
        return self._skills.journey_status(journey_id).model_dump()

    async def propose_offer(self, offer: Offer) -> dict:
        return await self._skills.propose_and_deliver(offer.model_dump(mode="json"))


class RuntimeScenarioDriver:
    def __init__(self, clock: FixedClock) -> None:
        self.clock = clock
        self.rt = CustomerRuntime(clock)
        self._build_sales()

    def _build_sales(self) -> None:
        # Sales reaches the Customer agent only through its skills and the sim only as `sales`.
        self.radar = SalesRadar(
            _InProcessCustomer(CustomerSkills(self.rt)),
            SimClient(inprocess_connector(self.rt.sim, "sales")),
        )

    def _sales_pass(self) -> None:
        """The Sales radar is a separate agent in production; the driver gives it a turn after
        every passenger-visible step so the harness stays synchronous and quiescent."""
        asyncio.run(self.radar.tick())

    def reset(self) -> None:
        self.rt.reset()
        self._build_sales()

    def trigger_cancellation(self, flight: str, reason: str) -> None:
        asyncio.run(self.rt.trigger_cancellation(flight, reason))
        self._sales_pass()

    def passenger_reply(self, text: str) -> None:
        asyncio.run(self.rt.handle_inbound(_PASSENGER_ID, text))
        self._sales_pass()

    def passenger_tap(self, button_id: str) -> None:
        rec = self.rt.journey_for_passenger(_PASSENGER_ID)
        if rec is None:
            raise LookupError("no journey yet")
        if button_id == "accept_upgrade":
            asyncio.run(accept_offer(self.rt, rec))
        elif button_id == "decline_upgrade":
            asyncio.run(decline_offer(self.rt, rec))
        else:
            raise ValueError(f"unknown button {button_id!r}")

    def get_journey(self) -> JourneyState:
        rec = self.rt.journey_for_passenger(_PASSENGER_ID)
        if rec is None:
            raise LookupError("no journey yet")
        return rec.state

    def transcript(self) -> list:
        sent = self.rt.messages.get(_PASSENGER_ID, [])
        if not sent:
            return []
        # Test-support model; imported lazily so production code never needs `tests`.
        from tests.scenario.driver import Message  # noqa: PLC0415

        offer_for = {}
        if (rec := self.rt.journey_for_passenger(_PASSENGER_ID)) is not None:
            offer_for = {
                e.payload["message_id"]: e.payload["offer_id"]
                for e in rec.state.timeline
                if e.type == "offer_sent"
            }
        return [
            Message(
                sender="passenger" if m.direction == "inbound" else "customer_agent",
                text=m.text,
                timestamp=m.timestamp,
                buttons=[_BUTTON_IDS.get(b, b) for b in m.buttons],
                attachments=m.attachments,
                offer_id=offer_for.get(m.message_id),
            )
            for m in sent
        ]

    def advance_clock(self, to: datetime) -> None:
        if to < self.clock.now():
            raise ValueError("the clock never moves backwards")
        self.clock.advance(to - self.clock.now())
        asyncio.run(self.rt.tick())
        self._sales_pass()


def make_driver(clock: FixedClock) -> RuntimeScenarioDriver:
    return RuntimeScenarioDriver(clock)
