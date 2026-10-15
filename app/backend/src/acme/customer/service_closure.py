"""The decision to close a journey that no offer was made on, and the closure itself (#229).

Kept apart from `closure.py` (the full verify-and-close flow) so mutation testing (#281) stays
focused on the decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from acme.customer.closure import close_loop
from acme.domain.journey import DisruptionStatus

if TYPE_CHECKING:
    from acme.customer.runtime import CustomerRuntime, JourneyRecord


LIVE_OR_DECIDED = frozenset({"proposed", "approved", "sent", "accepted", "declined"})


def service_closure_due(rt: CustomerRuntime, rec: JourneyRecord) -> bool:
    """A RESOLVED, acknowledged journey with no offer in play whose offer window has passed.

    Service completion must not depend on the revenue path (#229): with no offer (Sales down or
    suppressed, no seats, ineligible tier, guardrail rejection) nobody would ever verify and close
    the journey. A live offer (waiting for the passenger) or a decided one (the conversion flow
    closes those) is never closed over here. Retries after a failed attempt wait a further window.
    """
    state = rec.state
    if state.status is not DisruptionStatus.RESOLVED or not state.resolution_acknowledged:
        return False
    if state.open_requests or any(o.status in LIVE_OR_DECIDED for o in state.offers):
        return False
    anchors = [
        e.timestamp
        for e in state.timeline
        if e.type in ("resolution_acknowledged", "service_closure")
    ]
    return bool(anchors) and rt.clock.now() >= max(anchors) + rt.offer_window


async def close_without_offer(rt: CustomerRuntime, rec: JourneyRecord) -> bool:
    """Verify and close a journey no offer was made on. The caller holds the journey lock."""
    rec.state.append_event("customer_agent", "service_closure", {"reason": "offer_window_elapsed"})
    return await close_loop(rt, rec)
