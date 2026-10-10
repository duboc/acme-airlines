"""ScenarioDriver: the seam between the scenario acceptance tests and the real runtime.

The scenario tests (tests/scenario/test_s*.py) only talk to this protocol. The backend
issues (#10-#16, #28, #30) implement it against the real runtime (in-process MCP simulators,
FakeLlm, A2A agents) and expose a factory ``acme.runtime.scenario.make_driver(clock)`` that
returns a ``ScenarioDriver``. Implementations must not require changes to the tests.

Determinism contract: the driver must read time only from the injected ``FixedClock``, use
``LLM_MODE=fake`` and in-process MCP, make no network calls, and be synchronous from the
caller's view (every call returns after all agents have gone quiescent).

Timeline contract: ``JourneyState.timeline`` events use the ``EventType`` names below, with
the payload keys documented there. Event timestamps come from the injected clock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from acme.domain.journey import JourneyState

ACCEPT_UPGRADE_BUTTON = "accept_upgrade"
DECLINE_UPGRADE_BUTTON = "decline_upgrade"

Sender = Literal["customer_agent", "passenger", "sales"]


class EventType:
    """Timeline event types and payload keys asserted by the scenario tests."""

    CANCELLATION_DETECTED = "cancellation_detected"  # flight, reason
    PROFILE_LOADED = "profile_loaded"  # tier, seat, bags, connection{flight, departure}
    REBOOKED = "rebooked"  # flight, departure (ISO-8601)
    SEAT_HELD = "seat_held"  # flight, seat
    BAG_REROUTED = "bag_rerouted"  # flight, tag
    HOTEL_BOOKED = "hotel_booked"  # hotel_id
    TRANSFER_ARRANGED = "transfer_arranged"
    VOUCHER_ISSUED = "voucher_issued"  # kind
    CONNECTION_VERIFIED = "connection_verified"  # flight, connection_flight
    SALES_OPPORTUNITY = "sales_opportunity"  # flight, open_seats
    GUARDRAIL_CHECKED = "guardrail_decision"  # approved (bool), reasons, offer_id (#61 contract)
    OFFER_SENT = "offer_sent"  # offer_id
    UPGRADE_CONFIRMED = "upgrade_confirmed"  # flight, seat, cabin, price
    VERIFICATION_PASSED = "verification_passed"  # checks: flight, seat, hotel, baggage, upgrade
    MEMORY_COMMITTED = "memory_committed"
    CRM_LOGGED = "crm_logged"
    NPS_SCHEDULED = "nps_scheduled"
    ANALYTICS_RECORDED = "analytics_recorded"  # converted (bool)


class Message(BaseModel):
    """One passenger-visible chat message (the simulated WhatsApp transcript)."""

    model_config = ConfigDict(frozen=True)

    sender: Sender
    text: str
    timestamp: AwareDatetime
    buttons: list[str] = Field(default_factory=list)
    attachments: list[Literal["boarding_pass", "hotel_voucher", "meal_voucher"]] = Field(
        default_factory=list
    )
    offer_id: str | None = None  # set on the message that delivers an offer


@runtime_checkable
class ScenarioDriver(Protocol):
    """Drives one Mariana journey end to end and exposes what the passenger and ops see."""

    def reset(self) -> None:
        """Rebuild the seeded world (``mariana_world()``) and an empty journey/transcript."""
        ...

    def trigger_cancellation(self, flight: str, reason: str) -> None:
        """OCC publishes a cancellation; returns once all agents are quiescent."""
        ...

    def passenger_reply(self, text: str) -> None:
        """Mariana sends a free-text WhatsApp reply."""
        ...

    def passenger_tap(self, button_id: str) -> None:
        """Mariana taps a button (e.g. ``ACCEPT_UPGRADE_BUTTON``) on a delivered message."""
        ...

    def get_journey(self) -> JourneyState:
        """Current journey state, including offers and the append-only timeline."""
        ...

    def transcript(self) -> list[Message]:
        """Every passenger-visible message so far, in order (both directions)."""
        ...

    def advance_clock(self, to: datetime) -> None:
        """Move the fixed clock forward to an aware instant (never backwards)."""
        ...
