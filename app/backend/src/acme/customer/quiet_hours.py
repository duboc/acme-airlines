"""Quiet-hours rule (docs/product/policy-rules.md): pure, no I/O.

During quiet hours (default 22:00-07:00 passenger-local) the system sends nothing proactive
to the passenger until the journey has first reached RESOLVED. Passenger-initiated replies
may always be answered. (The single consolidated resolution message is #13; offers after
acknowledgement are guardrail rule 2, #6.)
"""

from dataclasses import dataclass
from datetime import datetime, time

from acme.domain.journey import DisruptionStatus, JourneyState


@dataclass(frozen=True)
class QuietHours:
    start: time = time(22)
    end: time = time(7)

    def contains(self, local: time) -> bool:
        if self.start <= self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end


def has_been_resolved(journey: JourneyState) -> bool:
    return journey.status is DisruptionStatus.RESOLVED or any(
        e.type == "status_changed" and e.payload.get("to") == DisruptionStatus.RESOLVED
        for e in journey.timeline
    )


def can_send_proactive(
    journey: JourneyState,
    now: datetime,
    quiet: QuietHours = QuietHours(),  # noqa: B008 (frozen, immutable default)
    passenger_initiated: bool = False,
) -> bool:
    """`now` must be aware and expressed in the passenger's local timezone."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware (passenger-local)")
    if passenger_initiated or not quiet.contains(now.timetz().replace(tzinfo=None)):
        return True
    return has_been_resolved(journey)
