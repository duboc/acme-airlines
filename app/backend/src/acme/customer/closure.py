"""Close the loop after the passenger's decision: verify, CRM, NPS, analytics, CLOSED, memory.

The caller holds the per-journey lock. Verification failure leaves the journey RESOLVED with a
`verification_failed` entry (details per failed check) and records nothing else.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from acme.customer.verify import verify_journey
from acme.domain.journey import DisruptionStatus
from acme.sim.client import ToolError

if TYPE_CHECKING:
    from acme.customer.runtime import CustomerRuntime, JourneyRecord

NPS_DELAY = timedelta(hours=24)  # after the trip: end of the last segment + 24h


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


async def close_loop(rt: CustomerRuntime, rec: JourneyRecord) -> bool:
    state = rec.state
    if state.status is not DisruptionStatus.RESOLVED:
        return False
    result = await verify_journey(rt, rec)
    if not result.ok:
        state.append_event(
            "customer_agent",
            "verification_failed",
            {"failed": result.failed, "details": result.details, "checks": result.checks},
        )
        return False
    state.append_event("customer_agent", "verification_passed", {"checks": result.checks})

    try:
        crm = await rt.client.call(
            "crm",
            "interaction.log",
            passenger_id=rec.passenger_id,
            channel="whatsapp",
            summary=f"Journey {state.journey_id} resolved and verified.",
        )
        last = rt.world.flights[rt.world.pnrs[rec.pnr_locator].segments[-1].flight_number]
        send_after = (last.scheduled_arrival or last.scheduled_departure) + NPS_DELAY
        nps = await rt.client.call(
            "scheduler", "survey.schedule",
            passenger_id=rec.passenger_id, send_after=send_after.isoformat(),
        )  # fmt: skip
    except ToolError as exc:
        state.append_event(
            "customer_agent", "needs_human", {"specialist": "closure", "reason": str(exc)[:200]}
        )
        return False
    state.append_event("customer_agent", "crm_logged", {"interaction_id": crm["interaction_id"]})
    state.append_event(
        "customer_agent",
        "nps_scheduled",
        {"survey_id": nps["survey_id"], "send_after": nps["send_after"]},
    )

    accepted = [r for r in state.offers if r.status == "accepted"]
    shown = [r for r in state.offers if r.status in ("accepted", "declined")]
    record = (accepted or shown or [None])[-1]
    state.append_event(
        "customer_agent",
        "analytics_recorded",
        {
            "event": "offer_accepted" if accepted else ("offer_declined" if shown else "no_offer"),
            "converted": bool(accepted),
            "offer_id": str(record.offer.offer_id) if record else None,
            "price": f"{record.offer.price:.2f}" if record else None,
            "margin": f"{record.offer.net_margin:.2f}" if record and accepted else None,
        },
    )
    state.transition(DisruptionStatus.CLOSED, actor="customer_agent")
    state.append_event(
        "customer_agent",
        "memory_committed",
        {"journey_id": state.journey_id, "events": len(state.timeline) + 1},
    )
    rt.store.save(state)
    return True
