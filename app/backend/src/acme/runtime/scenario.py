"""Real `ScenarioDriver` (see tests/scenario/driver.py): synchronous facade over the runtime.

Deterministic: fixed clock, in-process MCP simulators, no network, no LLM.
"""

import asyncio
from datetime import datetime

from acme.customer.runtime import CustomerRuntime
from acme.domain.clock import FixedClock
from acme.domain.fixtures import mariana_world
from acme.domain.journey import JourneyState

_PASSENGER_ID = next(iter(mariana_world().passengers))


class RuntimeScenarioDriver:
    def __init__(self, clock: FixedClock) -> None:
        self.clock = clock
        self.rt = CustomerRuntime(clock)

    def reset(self) -> None:
        self.rt.reset()

    def trigger_cancellation(self, flight: str, reason: str) -> None:
        asyncio.run(self.rt.trigger_cancellation(flight, reason))

    def passenger_reply(self, text: str) -> None:
        asyncio.run(self.rt.handle_inbound(_PASSENGER_ID, text))

    def passenger_tap(self, button_id: str) -> None:
        raise NotImplementedError("button taps arrive with #14+")

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

        return [
            Message(
                sender="passenger" if m.direction == "inbound" else "customer_agent",
                text=m.text,
                timestamp=m.timestamp,
                buttons=m.buttons,
                attachments=m.attachments,
            )
            for m in sent
        ]

    def advance_clock(self, to: datetime) -> None:
        if to < self.clock.now():
            raise ValueError("the clock never moves backwards")
        self.clock.advance(to - self.clock.now())
        asyncio.run(self.rt.tick())


def make_driver(clock: FixedClock) -> RuntimeScenarioDriver:
    return RuntimeScenarioDriver(clock)
