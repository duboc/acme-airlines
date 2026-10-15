"""Journey state machine, offers lifecycle and append-only timeline."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from acme import metrics
from acme.domain.clock import Clock, SystemClock
from acme.domain.models import Offer, Tier


class InvalidTransition(Exception):
    pass


class DisruptionStatus(StrEnum):
    DETECTED = "DETECTED"
    RESOLVING = "RESOLVING"
    RESOLVED = "RESOLVED"
    REPLANNING = "REPLANNING"
    CLOSED = "CLOSED"


S = DisruptionStatus
LEGAL: dict[S, frozenset[S]] = {
    S.DETECTED: frozenset({S.RESOLVING}),
    S.RESOLVING: frozenset({S.RESOLVED}),
    S.RESOLVED: frozenset({S.REPLANNING, S.CLOSED}),
    S.REPLANNING: frozenset({S.RESOLVED}),
    S.CLOSED: frozenset(),
}

OfferStatus = Literal[
    "proposed", "approved", "rejected", "sent", "invalidated", "accepted", "declined"
]
LIVE_OFFER_STATUSES = frozenset({"proposed", "approved", "sent"})


class PassengerRequest(BaseModel):
    request_id: str
    text: str
    resolved: bool = False


class OfferRecord(BaseModel):
    offer: Offer
    status: OfferStatus = "proposed"


class JourneyEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    actor: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class JourneyState:
    def __init__(
        self,
        journey_id: str,
        clock: Clock | None = None,
        *,
        passenger_tier: Tier | None = None,
        current_flight: str | None = None,
    ) -> None:
        self.journey_id = journey_id
        # Facts the offer guardrail checks claims against; None fails closed.
        self.passenger_tier = passenger_tier
        self.current_flight = current_flight
        self._clock = clock or SystemClock()
        self.status = S.DETECTED
        self.open_requests: list[PassengerRequest] = []
        self.resolution_acknowledged = False
        self.offers: list[OfferRecord] = []
        self._timeline: list[JourneyEvent] = []

    @classmethod
    def restore(
        cls,
        journey_id: str,
        *,
        status: DisruptionStatus,
        resolution_acknowledged: bool,
        open_requests: list[PassengerRequest],
        offers: list[OfferRecord],
        timeline: list[JourneyEvent],
        clock: Clock | None = None,
        passenger_tier: Tier | None = None,
        current_flight: str | None = None,
    ) -> "JourneyState":
        """Rebuild a persisted journey exactly (timeline included); see `acme.memory`."""
        state = cls(journey_id, clock, passenger_tier=passenger_tier, current_flight=current_flight)
        state.status = status
        state.resolution_acknowledged = resolution_acknowledged
        state.open_requests = list(open_requests)
        state.offers = list(offers)
        state._timeline = list(timeline)
        return state

    @property
    def timeline(self) -> tuple[JourneyEvent, ...]:
        """Read-only view; entries are frozen, the sequence cannot be edited."""
        return tuple(self._timeline)

    def append_event(self, actor: str, type: str, payload: dict[str, Any] | None = None) -> None:
        if type == "needs_human":
            metrics.needs_human(payload or {})
        elif type == "offer_proposed":
            metrics.offer("proposed")
        elif type == "upgrade_confirmed":  # not the tap (`accept_offer`): a refused one is no sale
            metrics.offer("accepted")
        elif type == "decline_offer":
            metrics.offer("declined")
        self._timeline.append(
            JourneyEvent(actor=actor, type=type, payload=payload or {}, timestamp=self._clock.now())
        )

    def transition(self, target: DisruptionStatus, actor: str = "system") -> None:
        current = self.status
        if target not in LEGAL[current]:
            raise InvalidTransition(f"{current} -> {target} is not allowed")
        if current is S.RESOLVED:
            for rec in self.offers:
                if rec.status in LIVE_OFFER_STATUSES:
                    rec.status = "invalidated"
        if target in (S.REPLANNING, S.RESOLVING):
            self.resolution_acknowledged = False
        self.status = target
        self.append_event(actor, "status_changed", {"from": current, "to": target})

    def add_offer(self, offer: Offer) -> OfferRecord:
        rec = OfferRecord(offer=offer)
        self.offers.append(rec)
        return rec
