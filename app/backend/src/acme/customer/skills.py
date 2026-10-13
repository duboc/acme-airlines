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

from decimal import Decimal
from typing import Any

from a2a.types import DataPart, Message, MessageSendParams, Role, TextPart
from opentelemetry.trace import SpanKind
from pydantic import BaseModel, ConfigDict, ValidationError

from acme import metrics
from acme.customer.runtime import CustomerRuntime, JourneyRecord
from acme.domain.journey import DisruptionStatus, OfferRecord
from acme.domain.models import CabinInventory, Offer
from acme.policy.guardrail_offer import evaluate_traced
from acme.sim.client import ToolError
from acme.sim.world import SimError
from acme.telemetry import is_safe_value, span

SKILLS = ("list_journeys", "journey_status", "propose_offer")


class SkillError(ValueError):
    """The request is invalid; nothing was recorded."""


class JourneyStatusView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    flight: str | None
    resolution_acknowledged: bool
    run_id: str  # opaque nonce of this journey run; no passenger or booking data


def ineligible_reason(rec: JourneyRecord) -> str | None:
    """Guardrail-style reason code (same names as `guardrail_offer`), or None if eligible."""
    state = rec.state
    if state.status is not DisruptionStatus.RESOLVED:
        return "disruption_not_resolved"
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
            payload = {"journey_status": state.status.value, "reason": reason}  # typed: #61
            last = state.timeline[-1] if state.timeline else None
            if not (last and last.type == "sales_suppressed" and last.payload == payload):
                state.append_event("customer_agent", "sales_suppressed", payload)
        flight = state.current_flight or self._current_flight(rec)
        return JourneyStatusView(
            status=state.status.value,
            flight=flight,
            resolution_acknowledged=state.resolution_acknowledged,
            run_id=rec.run_id,
        )

    def propose_offer(
        self, payload: dict[str, Any], *, inventory: CabinInventory | None = None
    ) -> dict[str, Any]:
        """Validate, run the guardrail and record the decision (no delivery; see below).

        Raises SkillError for invalid offers and for proposals while the journey is not
        RESOLVED (nothing is recorded as an offer, #83). Other rejections are recorded as
        `rejected` and returned as reason codes only.
        """
        try:
            offer = Offer.model_validate(payload)
        except ValidationError as exc:
            raise SkillError(f"invalid offer: {exc.error_count()} validation error(s)") from None
        rec = self._journey(offer.journey_id)
        state = rec.state
        if any(r.offer.offer_id == offer.offer_id for r in state.offers):
            raise SkillError("duplicate offer_id")
        state.current_flight = self._current_flight(rec)  # Customer-owned fact
        inventory = inventory or self._world_inventory(offer)
        state.append_event(
            "customer_agent",
            "sales_opportunity",
            {"flight": offer.flight, "open_seats": inventory.open_seats},
        )
        decision = evaluate_traced(state, offer, inventory, "propose")
        self._decision_event(state, offer, decision)
        if "disruption_not_resolved" in decision.reasons:
            raise SkillError("rejected: disruption_not_resolved")
        record = state.add_offer(offer)
        record.status = "approved" if decision.approved else "rejected"
        state.append_event(
            "customer_agent",
            "offer_proposed",
            {"offer_id": str(offer.offer_id), "flight": offer.flight},
        )
        return {
            "status": record.status if decision.approved else "rejected",
            "offer_id": str(offer.offer_id),
            "reasons": list(decision.reasons),
        }

    async def propose_and_deliver(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The A2A path: guardrail at proposal, then at send time, atomically per journey."""
        try:
            journey_id = str(payload["journey_id"])
        except (KeyError, TypeError):
            raise SkillError("invalid offer") from None
        async with self._rt.journey_lock(journey_id):
            rec = self._journey(journey_id)
            inventory = await self._load_facts(rec, payload)
            result = self.propose_offer(payload, inventory=inventory)
            if result["status"] != "approved":
                return result
            record = rec.state.offers[-1]
        # The model call runs with NO lock held (#231); delivery re-validates under it.
        inventory, text, key = await self._prepare_text(rec, record)
        async with self._rt.journey_lock(journey_id):
            decision = await self._deliver(rec, record, inventory, text, key)
            if decision.approved:
                return result
            return {**result, "status": "rejected", "reasons": list(decision.reasons)}

    # --- delivery ----------------------------------------------------------------------------
    async def _load_facts(self, rec: JourneyRecord, payload: dict[str, Any]) -> CabinInventory:
        """Customer-owned facts: tier from loyalty, inventory from the inventory server."""
        rt = self._rt
        member = await rt.client.call("loyalty", "member.get", passenger_id=rec.passenger_id)
        rec.state.passenger_tier = member["tier"]
        flight, cabin = str(payload.get("flight")), str(payload.get("cabin"))
        try:
            seats = await rt.client.call(
                "inventory", "cabin_inventory.get", flight=flight, cabin=cabin
            )
            fare = await rt.client.call("inventory", "upgrade_fare.get", flight=flight, cabin=cabin)
            return CabinInventory(
                flight_number=flight,
                cabin=cabin,  # type: ignore[arg-type]
                open_seats=seats["open_seats"],
                standard_upgrade_fare=Decimal(fare["standard_fare"]),
                cost_floor=Decimal(fare["cost_floor"]),
            )
        except (ToolError, ValidationError):  # no such inventory: the guardrail rejects it
            return CabinInventory(
                flight_number=flight, cabin="premium_business", open_seats=0,
                standard_upgrade_fare=Decimal(0), cost_floor=Decimal(0),
            )  # fmt: skip

    def _world_inventory(self, offer: Offer) -> CabinInventory:
        try:
            return self._rt.sim.cabin_inventory(offer.flight, offer.cabin)
        except SimError:
            return CabinInventory(
                flight_number=offer.flight, cabin="premium_business", open_seats=0,
                standard_upgrade_fare=Decimal(0), cost_floor=Decimal(0),
            )  # fmt: skip

    def _current_flight(self, rec: JourneyRecord) -> str | None:
        """The rebooked flight: first PNR segment that is still operating (Customer data)."""
        world = self._rt.world
        return next(
            (
                s.flight_number
                for s in world.pnrs[rec.pnr_locator].segments
                if world.flights[s.flight_number].status != "cancelled"
            ),
            None,
        )

    @staticmethod
    def _decision_event(state: Any, offer: Offer, decision: Any) -> None:
        state.append_event(
            "guardrail",
            "guardrail_decision",
            {
                "approved": decision.approved,
                "reasons": list(decision.reasons),
                "offer_id": str(offer.offer_id),
            },
        )

    async def _prepare_text(self, rec: JourneyRecord, record: OfferRecord) -> tuple[Any, str, Any]:
        """Facts and passenger text for an approved offer. Lock-free: the model call may be slow."""
        inventory = await self._load_facts(rec, record.offer.model_dump(mode="json"))
        text, _ = await self._compose(rec, record.offer, inventory)
        return inventory, text, self._facts_key(rec, inventory)

    @staticmethod
    def _facts_key(rec: JourneyRecord, inventory: CabinInventory) -> tuple[Any, ...]:
        return (
            rec.state.current_flight, inventory.open_seats, inventory.standard_upgrade_fare,
            rec.hotel.hotel_id if rec.hotel else None,
        )  # fmt: skip

    async def _deliver(
        self, rec: JourneyRecord, record: OfferRecord, inventory: Any, text: str, key: Any
    ) -> Any:
        """Under the journey lock: re-read the facts, and if they moved while the model call was
        in flight send the deterministic template of the current ones. Re-run the guardrail
        immediately before sending, send."""
        rt, state, offer = self._rt, rec.state, record.offer
        inventory = await self._load_facts(rec, offer.model_dump(mode="json"))
        state.current_flight = self._current_flight(rec)
        if self._facts_key(rec, inventory) != key:
            text, _ = await self._compose(rec, offer, inventory, use_llm=False)
        # time-of-use re-check: no await until send
        decision = evaluate_traced(state, offer, inventory, "send")
        if not decision.approved:
            record.status = "invalidated"
            self._decision_event(state, offer, decision)
            return decision
        msg = await rt.send(
            rec.passenger_id,
            text,
            attachments=["boarding_pass", "hotel_voucher"],
            buttons=["Accept", "No thanks"],
        )
        record.status = "sent"
        state.append_event(
            "customer_agent",
            "offer_sent",
            {"offer_id": str(offer.offer_id), "message_id": msg.message_id},
        )
        return decision

    async def _compose(
        self, rec: JourneyRecord, offer: Offer, inventory: CabinInventory, use_llm: bool = True
    ) -> tuple[str, dict[str, Any]]:
        """Passenger text from Customer-owned data only. From the offer: `price` (a Decimal that
        the guardrail bounded). Flight, cabin and seat come from Customer journey/inventory data;
        no Sales-authored string is rendered or sent to the LLM."""
        rt = self._rt
        profile = await rt.client.call("crm", "profile.get", passenger_id=rec.passenger_id)
        name = profile["name"].split()[0]
        flight = rec.state.current_flight or ""
        open_premium = rt.world.flights[flight].open_seats.get(inventory.cabin, [])
        seat = f" {open_premium[0]}" if open_premium else ""
        price = f"${offer.price:.2f}"
        hotel = f" and your hotel voucher ({rec.hotel.hotel_id})" if rec.hotel else ""
        summary = (
            f"your boarding pass for {flight}{hotel} are attached. "
            "Only if you would like it, and with no pressure at all, there is an optional upgrade: "
            f"a premium business (lie-flat) seat{seat} on {flight} for {price} instead of the "
            f"usual ${inventory.standard_upgrade_fare:.2f}. Tap Accept to take it, or No thanks "
            "to keep your current seat."
        )
        fallback = f"Hi {name}, {summary}"
        context = {
            "passenger": name,
            "summary": summary,
            "offer": {"flight": flight, "cabin": "premium business", "seat": seat.strip(),
                      "price": price},
        }  # fmt: skip
        try:
            if not use_llm:
                raise ValueError("stale facts: template of the current ones")
            text = await rt.llm.compose_passenger_message(context)
            if not all(f in text for f in (flight, price, "optional", "premium business")):
                raise ValueError("missing offer fact")
        except Exception:  # noqa: BLE001 (any LLM failure falls back to the template)
            text = fallback
            metrics.template_fallback("offer")
        return text, context

    # --- A2A transport -------------------------------------------------------------------------
    async def handle_a2a(self, request: dict[str, Any]) -> dict[str, Any]:
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
            result = await self._dispatch(data["skill"], data["args"])
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

    async def _dispatch(self, skill: str, args: dict[str, Any]) -> dict[str, Any]:
        # spans carry skill + journey id only, never the offer or any message text
        if skill == "list_journeys" and not args:
            with span("acme.a2a.list_journeys", kind=SpanKind.SERVER, acme__skill=skill):
                return {"journey_ids": self.list_journeys()}
        if skill == "journey_status" and set(args) == {"journey_id"}:
            with span(
                "acme.a2a.journey_status",
                kind=SpanKind.SERVER,
                acme__skill=skill,
                **_jid(args["journey_id"]),
            ):
                return self.journey_status(str(args["journey_id"])).model_dump()
        if skill == "propose_offer" and set(args) == {"offer"} and isinstance(args["offer"], dict):
            with span(
                "acme.a2a.propose_offer",
                kind=SpanKind.SERVER,
                acme__skill=skill,
                **_jid(args["offer"].get("journey_id")),
            ):
                return await self.propose_and_deliver(args["offer"])
        raise SkillError(f"unknown skill or bad arguments: {skill!r}")


def _jid(value: object) -> dict[str, str]:
    """The journey id as a span attribute, only if it looks like an id (never echo input)."""
    return {"acme__journey_id": value} if isinstance(value, str) and is_safe_value(value) else {}


__all__ = ["SKILLS", "CustomerSkills", "JourneyStatusView", "SkillError", "TextPart"]
