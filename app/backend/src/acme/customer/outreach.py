"""Scenario 3a: the one proactive, consolidated WhatsApp message and inbound routing.

Outreach content comes from Customer-owned journey data only. Wording is composed by
`LlmPort.compose_passenger_message`; every resolution fact is validated in code before sending,
and a deterministic template is sent instead if the LLM fails or omits a fact.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import time
from typing import TYPE_CHECKING, Any

from acme import metrics
from acme.customer.intent import classify  # noqa: F401 - re-exported for callers
from acme.customer.quiet_hours import can_send_proactive
from acme.domain.fixtures import GRU
from acme.domain.journey import DisruptionStatus, PassengerRequest
from acme.llm.port import Acknowledge, DepartureAfter, StructuredRequest
from acme.telemetry import specialist_span

if TYPE_CHECKING:
    from acme.customer.runtime import CustomerRuntime, JourneyRecord


def _last(rec: JourneyRecord, type_: str) -> dict[str, Any] | None:
    found = [e.payload for e in rec.state.timeline if e.type == type_]
    return found[-1] if found else None


def collect_facts(rec: JourneyRecord) -> dict[str, Any] | None:
    """Resolution facts from the journey timeline; None if the resolution is incomplete.

    What must be there follows the entitlements the rights specialist assessed (DOC-05..08): a
    hotel, a transfer and meal vouchers are required only when they were due.
    """
    detected, rebooked = _last(rec, "cancellation_detected"), _last(rec, "rebooked")
    seat, hotel = _last(rec, "seat_held"), _last(rec, "hotel_booked")
    transfer = _last(rec, "transfer_arranged")
    voucher = _last(rec, "voucher_issued")
    if not (detected and rebooked and seat):
        return None
    due = _last(rec, "duty_of_care_assessed") or {}  # no assessment: the full DOC-05 care
    if due.get("hotel_required", True) and not hotel:
        return None
    if due.get("ground_transfer_required", True) and not transfer:
        return None
    if due.get("meal_vouchers", True) and not voucher:
        return None
    departure = rebooked["departure"]
    return {
        "cancelled_flight": detected["flight"],
        "new_flight": rebooked["flight"],
        "departure": departure[11:16],  # local HH:MM from the ISO string
        "seat": seat["seat"],
        "hotel_id": hotel["hotel_id"] if hotel else None,
        "has_transfer": transfer is not None,
        "has_voucher": voucher is not None,
    }


def required_facts(facts: dict[str, Any]) -> list[str]:
    needed = [facts["cancelled_flight"], facts["new_flight"], facts["departure"], facts["seat"]]
    if facts["hotel_id"]:
        needed.append(facts["hotel_id"])
    if facts["has_transfer"]:
        needed.append("transfer")
    if facts["has_voucher"]:
        needed.append("voucher")
    return needed


def _care_sentence(f: dict[str, Any]) -> str:
    extras = [
        x
        for x, on in (("a transfer", f["has_transfer"]), ("meal vouchers", f["has_voucher"]))
        if on
    ]
    arranged = f"{' and '.join(extras)} arranged"
    if f["hotel_id"]:
        return f"Your hotel {f['hotel_id']} is booked" + (
            f", with {arranged}. " if extras else ". "
        )
    return f"I have {arranged}. " if extras else ""


def template_text(name: str, f: dict[str, Any]) -> str:
    return (
        f"Hi {name}, I'm sorry: flight {f['cancelled_flight']} was cancelled. "
        f"I have already rebooked you on {f['new_flight']}, departing at {f['departure']}, "
        f"with your seat {f['seat']} kept and your bag re-routed. "
        f"{_care_sentence(f)}"
        "Reply here if you need anything changed."
    )


@dataclass(frozen=True)
class PreparedNotification:
    facts: dict[str, Any]
    name: str
    text: str
    source: str


async def prepare_notification(
    rt: CustomerRuntime, rec: JourneyRecord
) -> PreparedNotification | None:
    """Gather facts and compose the text. Takes NO lock: the model call may be slow (#231)."""
    facts = collect_facts(rec)
    if facts is None:
        return None
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
    return PreparedNotification(facts, name, text, source)


async def notify_resolution(rt: CustomerRuntime, rec: JourneyRecord) -> None:
    """Prepare then send (callers that need the lock-free model call use the two halves)."""
    if prepared := await prepare_notification(rt, rec):
        await send_notification(rt, rec, prepared)


async def send_notification(
    rt: CustomerRuntime, rec: JourneyRecord, prepared: PreparedNotification
) -> None:
    """Commit under the journey lock: re-validate, because the state may have moved while the
    model call was in flight. No longer due: send nothing. Facts changed: the text was composed
    against stale facts, so send the deterministic template of the current ones instead."""
    if not notification_due(rt, rec):
        return
    facts = collect_facts(rec)
    if facts is None:
        return
    text, source = prepared.text, prepared.source
    if facts != prepared.facts:
        text, source = template_text(prepared.name, facts), "template"
    attachments = [
        name
        for name, issued in (
            ("hotel_voucher", facts["hotel_id"]),
            ("meal_voucher", facts["has_voucher"]),
        )
        if issued
    ]
    msg = await rt.send(rec.passenger_id, text, attachments=attachments)
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


async def handle_inbound(
    rt: CustomerRuntime, rec: JourneyRecord, text: str, intent: StructuredRequest | None = None
) -> None:
    """Apply a passenger message. The caller holds the journey lock; pass `intent` (from
    `classify`, computed before taking it) so the model call is not made under the lock."""
    state = rec.state
    state.append_event("passenger", "passenger_message", {"text": text})
    if intent is None:
        intent = await classify(rt, text)
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
