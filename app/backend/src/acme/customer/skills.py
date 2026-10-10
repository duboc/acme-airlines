"""Customer agent A2A skills: the ONLY surface the Sales agent can reach (governance layer 1).

- `list_journeys()` -> open journey ids. No PII.
- `journey_status(id)` -> `{status, flight, resolution_acknowledged}`. No PII (no name, phone, PNR,
  seat or tier). If the journey is not eligible for an offer, a `sales_suppressed` entry is written
  to its timeline (by the Customer side; Sales writes nothing).
- `propose_offer(offer)` -> accepts ONLY the structured `Offer` (`extra="forbid"`, no free text).
  Here it only validates and records the proposal as `proposed`; guardrail evaluation is #15.

Transport: a minimal A2A JSON-RPC `message/send` handler. The message must contain exactly one
`DataPart` `{"skill": ..., "args": {...}}`; any text part, extra key or unknown skill is rejected.
"""

from __future__ import annotations

from typing import Any

from a2a.types import DataPart, Message, MessageSendParams, Role, TextPart
from pydantic import BaseModel, ConfigDict, ValidationError

from acme.customer.runtime import CustomerRuntime, JourneyRecord
from acme.domain.journey import DisruptionStatus
from acme.domain.models import Offer

SKILLS = ("list_journeys", "journey_status", "propose_offer")


class SkillError(ValueError):
    """The request is invalid; nothing was recorded."""


class JourneyStatusView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    flight: str | None
    resolution_acknowledged: bool


def ineligible_reason(rec: JourneyRecord) -> str | None:
    state = rec.state
    if state.status is not DisruptionStatus.RESOLVED:
        return state.status.value
    if not state.resolution_acknowledged:
        return "resolution_not_acknowledged"
    return None


class CustomerSkills:
    def __init__(self, runtime: CustomerRuntime) -> None:
        self._rt = runtime

    def _journey(self, journey_id: str) -> JourneyRecord:
        try:
            return self._rt.journeys[journey_id]
        except KeyError:
            raise SkillError(f"unknown journey {journey_id}") from None

    def list_journeys(self) -> list[str]:
        return [
            j for j, r in self._rt.journeys.items() if r.state.status is not DisruptionStatus.CLOSED
        ]

    def journey_status(self, journey_id: str) -> JourneyStatusView:
        rec = self._journey(journey_id)
        state = rec.state
        if reason := ineligible_reason(rec):
            last = state.timeline[-1] if state.timeline else None
            if not (last and last.type == "sales_suppressed" and last.payload["reason"] == reason):
                state.append_event("customer_agent", "sales_suppressed", {"reason": reason})
        flight = state.current_flight
        if flight is None:  # the rebooked flight: first segment of the PNR still operating
            pnr = self._rt.world.pnrs[rec.pnr_locator]
            flight = next(
                (
                    s.flight_number
                    for s in pnr.segments
                    if self._rt.world.flights[s.flight_number].status != "cancelled"
                ),
                None,
            )
        return JourneyStatusView(
            status=state.status.value,
            flight=flight,
            resolution_acknowledged=state.resolution_acknowledged,
        )

    def propose_offer(self, payload: dict[str, Any]) -> dict[str, str]:
        try:
            offer = Offer.model_validate(payload)
        except ValidationError as exc:
            raise SkillError(f"invalid offer: {exc.error_count()} validation error(s)") from None
        rec = self._journey(offer.journey_id)
        if any(r.offer.offer_id == offer.offer_id for r in rec.state.offers):
            raise SkillError("duplicate offer_id")
        rec.state.add_offer(offer)
        rec.state.append_event(
            "customer_agent",
            "offer_proposed",
            {"offer_id": str(offer.offer_id), "flight": offer.flight},
        )
        return {"status": "proposed", "offer_id": str(offer.offer_id)}

    # --- A2A transport -------------------------------------------------------------------------
    def handle_a2a(self, request: dict[str, Any]) -> dict[str, Any]:
        rid = request.get("id")
        try:
            if request.get("method") != "message/send":
                raise SkillError("only message/send is supported")
            params = MessageSendParams.model_validate(request.get("params"))
            parts = [p.root for p in params.message.parts]
            if len(parts) != 1 or not isinstance(parts[0], DataPart):
                raise SkillError("exactly one data part is required; text parts are not accepted")
            data = parts[0].data
            if set(data) != {"skill", "args"} or not isinstance(data["args"], dict):
                raise SkillError("data part must be exactly {skill, args}")
            result = self._dispatch(data["skill"], data["args"])
        except (SkillError, ValidationError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32602, "message": str(exc)[:200]},
            }
        reply = Message(
            message_id=f"reply-{rid}",
            role=Role.agent,
            parts=[DataPart(data=result)],  # type: ignore[arg-type]
        )
        return {"jsonrpc": "2.0", "id": rid, "result": reply.model_dump(mode="json", by_alias=True)}

    def _dispatch(self, skill: str, args: dict[str, Any]) -> dict[str, Any]:
        if skill == "list_journeys" and not args:
            return {"journey_ids": self.list_journeys()}
        if skill == "journey_status" and set(args) == {"journey_id"}:
            return self.journey_status(str(args["journey_id"])).model_dump()
        if skill == "propose_offer" and set(args) == {"offer"} and isinstance(args["offer"], dict):
            return self.propose_offer(args["offer"])
        raise SkillError(f"unknown skill or bad arguments: {skill!r}")


__all__ = ["SKILLS", "CustomerSkills", "JourneyStatusView", "SkillError", "TextPart"]
