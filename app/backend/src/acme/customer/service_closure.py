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


_DECIDED = frozenset({"accepted", "declined"})


def decided_closure_due(rt: CustomerRuntime, rec: JourneyRecord) -> bool:
    """A journey whose passenger DECIDED an offer but whose closure failed (verification, CRM or
    scheduler), still RESOLVED: retry the closure once a further window has passed since the
    failure (#445). Without this a decided offer blocks `service_closure_due` for ever."""
    state = rec.state
    if state.status is not DisruptionStatus.RESOLVED or state.open_requests:
        return False
    if any(o.status in LIVE_OR_DECIDED - _DECIDED for o in state.offers):
        return False
    if not any(o.status in _DECIDED for o in state.offers):
        return False
    failures = [
        e.timestamp
        for e in state.timeline
        if e.type == "verification_failed"
        or (e.type == "needs_human" and e.payload.get("specialist") == "closure")
    ]
    return bool(failures) and rt.clock.now() >= max(failures) + rt.offer_window


async def retry_decided_closure(rt: CustomerRuntime, rec: JourneyRecord) -> bool:
    """Close again. No `service_closure` event: the offer's outcome stays as the passenger left
    it. The caller holds the journey lock."""
    return await close_loop(rt, rec)


async def close_without_offer(rt: CustomerRuntime, rec: JourneyRecord) -> bool:
    """Verify and close a journey no offer was made on. The caller holds the journey lock."""
    rec.state.append_event("customer_agent", "service_closure", {"reason": "offer_window_elapsed"})
    return await close_loop(rt, rec)
