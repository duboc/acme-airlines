"""Offer guardrail ("Two Goals, One Guardrail"): pure, deterministic, no LLM.

`evaluate(journey, offer, inventory)` approves a Sales offer only if ALL six rules hold.
Every failed rule is reported (deduplicated, in rule order). Approval lists each
satisfied rule. Same six rules as ADR 0100:

1. journey is RESOLVED                                  -> disruption_not_resolved
2. resolution acknowledged and no open requests         -> resolution_not_acknowledged,
                                                           open_request_pending
3. genuine discounted benefit for this passenger        -> price_claim_mismatch, no_inventory,
                                                           not_a_genuine_discount, tier_not_eligible
4. margin recomputed from inventory (price - floor)     -> negative_margin, price_claim_mismatch
5. zero pressure: optional, one live/answered offer     -> not_optional, offer_already_made
6. origin is "sales" and flight is the current flight   -> wrong_origin, flight_mismatch

Fail-closed choices:
- The inventory must describe the offered flight and cabin, else `no_inventory`.
- A journey without a known tier is `tier_not_eligible`; without a current flight,
  `flight_mismatch`.
- The offer under evaluation is excluded from rule 5 (matched by `offer_id`), so the send-time
  and accept-time re-checks need no bypass.

`offer.origin` is only a payload claim. The caller's real identity is enforced by the network
and IAM layers (#27, #24), not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from acme import metrics
from acme.domain.journey import DisruptionStatus, JourneyState
from acme.domain.models import CabinInventory, Offer
from acme.policy.margin_opt import recovery_quote
from acme.telemetry import set_attributes, span

_BLOCKING_OTHER_STATUSES = frozenset({"approved", "sent", "accepted", "declined"})


@dataclass(frozen=True)
class GuardrailDecision:
    approved: bool
    reasons: list[str] = field(default_factory=list)


def evaluate(journey: JourneyState, offer: Offer, inventory: CabinInventory) -> GuardrailDecision:
    failed: list[str] = []
    ok: list[str] = []

    def rule(name: str, failures: list[str]) -> None:
        failed.extend(f for f in failures if f not in failed)
        if not failures:
            ok.append(name)

    # 1
    rule(
        "disruption_resolved",
        [] if journey.status is DisruptionStatus.RESOLVED else ["disruption_not_resolved"],
    )

    # 2
    f2 = []
    if not journey.resolution_acknowledged:
        f2.append("resolution_not_acknowledged")
    if journey.open_requests:
        f2.append("open_request_pending")
    rule("resolution_acknowledged", f2)

    # 3
    f3 = []
    if offer.standard_price != inventory.standard_upgrade_fare:
        f3.append("price_claim_mismatch")
    if (
        inventory.flight_number != offer.flight
        or inventory.cabin != offer.cabin
        or inventory.open_seats <= 0
    ):
        f3.append("no_inventory")
    quote = recovery_quote(inventory, journey.passenger_tier)
    if offer.price >= inventory.standard_upgrade_fare or (
        quote is not None and offer.price > quote.price
    ):
        f3.append("not_a_genuine_discount")
    if quote is None:
        f3.append("tier_not_eligible")
    rule("genuine_discount", f3)

    # 4
    f4 = []
    net = offer.price - inventory.cost_floor
    if net < 0:
        f4.append("negative_margin")
    if net != offer.net_margin:
        f4.append("price_claim_mismatch")
    rule("margin_verified", f4)

    # 5
    f5 = []
    if offer.optional is not True:
        f5.append("not_optional")
    for rec in journey.offers:
        if rec.offer.offer_id == offer.offer_id:
            continue
        if rec.status in _BLOCKING_OTHER_STATUSES or (
            rec.status == "invalidated" and rec.offer.flight == offer.flight
        ):
            f5.append("offer_already_made")
            break
    rule("zero_pressure", f5)

    # 6
    f6 = []
    if offer.origin != "sales":
        f6.append("wrong_origin")
    if journey.current_flight != offer.flight:
        f6.append("flight_mismatch")
    rule("origin_and_flight_verified", f6)

    return GuardrailDecision(approved=not failed, reasons=failed if failed else ok)


def evaluate_traced(
    journey: JourneyState, offer: Offer, inventory: CabinInventory, step: str
) -> GuardrailDecision:
    """`evaluate` inside an `acme.guardrail.decision` span (decision + first reason code only).
    `step` names the call site: propose, send, accept or recheck."""
    with span(
        "acme.guardrail.decision",
        acme__journey_id=journey.journey_id,
        acme__flight=offer.flight,
        acme__step=step,
    ) as s:
        decision = evaluate(journey, offer, inventory)
        metrics.guardrail_decision(decision.approved, decision.reasons, step)
        set_attributes(
            s,
            acme__guardrail__decision="approve" if decision.approved else "reject",
            acme__guardrail__reason_code=decision.reasons[0],
        )
        return decision
