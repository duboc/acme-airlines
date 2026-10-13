"""Scenario 3a: the one proactive, consolidated WhatsApp message and inbound routing.

Outreach content comes from Customer-owned journey data only. Wording is composed by
`LlmPort.compose_passenger_message`; every resolution fact is validated in code before sending,
and a deterministic template is sent instead if the LLM fails or omits a fact.
"""

from __future__ import annotations

import uuid
from datetime import time
from typing import TYPE_CHECKING, Any

from acme import metrics
from acme.customer.quiet_hours import can_send_proactive
from acme.domain.fixtures import GRU
from acme.domain.journey import DisruptionStatus, PassengerRequest
from acme.llm.port import Acknowledge, DepartureAfter
from acme.telemetry import specialist_span

if TYPE_CHECKING:
    from acme.customer.runtime import CustomerRuntime, JourneyRecord


def _last(rec: JourneyRecord, type_: str) -> dict[str, Any] | None:
    found = [e.payload for e in rec.state.timeline if e.type == type_]
    return found[-1] if found else None


def collect_facts(rec: JourneyRecord) -> dict[str, Any] | None:
    """Resolution facts from the journey timeline; None if the resolution is incomplete."""
    detected, rebooked = _last(rec, "cancellation_detected"), _last(rec, "rebooked")
    seat, hotel = _last(rec, "seat_held"), _last(rec, "hotel_booked")
    transfer = _last(rec, "transfer_arranged")
    voucher = _last(rec, "voucher_issued")
    if not (detected and rebooked and seat and hotel and transfer and voucher):
        return None
    departure = rebooked["departure"]
    return {
        "cancelled_flight": detected["flight"],
        "new_flight": rebooked["flight"],
        "departure": departure[11:16],  # local HH:MM from the ISO string
        "seat": seat["seat"],
        "hotel_id": hotel["hotel_id"],
        "has_transfer": True,
        "has_voucher": True,
    }


def required_facts(facts: dict[str, Any]) -> list[str]:
    return [
        facts["cancelled_flight"], facts["new_flight"], facts["departure"], facts["seat"],
        facts["hotel_id"], "transfer", "voucher",
    ]  # fmt: skip


def template_text(name: str, f: dict[str, Any]) -> str:
    return (
        f"Hi {name}, I'm sorry: flight {f['cancelled_flight']} was cancelled. "
        f"I have already rebooked you on {f['new_flight']}, departing at {f['departure']}, "
        f"with your seat {f['seat']} kept and your bag re-routed. "
        f"Your hotel {f['hotel_id']} is booked, with a transfer and meal vouchers arranged. "
        "Reply here if you need anything changed."
    )


async def notify_resolution(rt: CustomerRuntime, rec: JourneyRecord) -> None:
    facts = collect_facts(rec)
    if facts is None:
        return
    profile = await rt.client.call("crm", "profile.get", passenger_id=rec.passenger_id)
    name = profile["name"].split()[0]
    fallback = template_text(name, facts)
    context = {"passenger": name, "facts": facts, "summary": fallback.split(", ", 1)[1]}
    source = "llm"
    try:
        text = await rt.llm.compose_passenger_message(context)
        if not all(fact in text for fact in required_facts(facts)):
            raise ValueError("missing resolution fact")
    except Exception:  # noqa: BLE001 (any LLM failure falls back to the template)
        text, source = fallback, "template"
        metrics.template_fallback("resolution")
    msg = await rt.send(rec.passenger_id, text, attachments=["hotel_voucher", "meal_voucher"])
    resolved_at = max(
        (
            e.timestamp
            for e in rec.state.timeline
            if e.type == "status_changed" and e.payload.get("to") == DisruptionStatus.RESOLVED
        ),
        default=None,
    )
    if resolved_at is not None:
        metrics.notification_latency((rt.clock.now() - resolved_at).total_seconds())
    rec.state.append_event(
        "customer_agent", "notification_sent", {"message_id": msg.message_id, "source": source}
    )


def notification_due(rt: CustomerRuntime, rec: JourneyRecord) -> bool:
    state, now = rec.state, rt.clock.now()
    if state.status is not DisruptionStatus.RESOLVED:
        return False
    if any(e.type == "notification_sent" for e in state.timeline):
        return False
    resolved_at = max(
        e.timestamp
        for e in state.timeline
        if e.type == "status_changed" and e.payload.get("to") == DisruptionStatus.RESOLVED
    )
    return now >= resolved_at + rt.notify_delay and can_send_proactive(state, now.astimezone(GRU))


async def handle_inbound(rt: CustomerRuntime, rec: JourneyRecord, text: str) -> None:
    state = rec.state
    state.append_event("passenger", "passenger_message", {"text": text})
    try:
        intent = await rt.llm.parse_passenger_request(text)
    except Exception:  # noqa: BLE001
        from acme.llm.port import Other  # noqa: PLC0415

        intent = Other(text=text)
    if isinstance(intent, DepartureAfter):
        await _departure_after(rt, rec, intent.time)
    elif isinstance(intent, Acknowledge):
        if not state.open_requests and state.status is DisruptionStatus.RESOLVED:
            state.resolution_acknowledged = True
            state.append_event("passenger", "resolution_acknowledged", {})
    else:
        state.open_requests.append(
            PassengerRequest(request_id=uuid.uuid4().hex[:8], text=intent.text)
        )
        state.append_event(
            "customer_agent",
            "needs_human",
            {"specialist": "customer", "reason": "request needs a person"},
        )
        await rt.send(
            rec.passenger_id,
            "I can't handle that automatically, so I've asked a colleague to follow up with you.",
        )


async def _departure_after(rt: CustomerRuntime, rec: JourneyRecord, after: time) -> None:
    state = rec.state
    request = PassengerRequest(
        request_id=uuid.uuid4().hex[:8], text=f"departure_after {after:%H:%M}"
    )
    state.open_requests.append(request)
    if state.status is DisruptionStatus.RESOLVED:
        state.transition(DisruptionStatus.REPLANNING, actor="customer_agent")
        with specialist_span("change", state.journey_id):
            await rt.change_agent.handle(rt, rec, request.request_id, after)
