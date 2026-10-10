"""Journey state machine, offers lifecycle and append-only timeline."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from acme.domain.clock import Clock, SystemClock
from acme.domain.models import Offer


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
    def __init__(self, journey_id: str, clock: Clock | None = None) -> None:
        self.journey_id = journey_id
        self._clock = clock or SystemClock()
        self.status = S.DETECTED
        self.open_requests: list[PassengerRequest] = []
        self.resolution_acknowledged = False
        self.offers: list[OfferRecord] = []
        self._timeline: list[JourneyEvent] = []

    @property
    def timeline(self) -> tuple[JourneyEvent, ...]:
        """Read-only view; entries are frozen, the sequence cannot be edited."""
        return tuple(self._timeline)

    def append_event(self, actor: str, type: str, payload: dict[str, Any] | None = None) -> None:
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
