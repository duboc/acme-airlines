"""Scenario 5a: one-tap conversion (accept -> hold -> charge -> assign -> boarding pass).

Everything below runs under the SAME per-journey lock as offer delivery (#15). The guardrail is
re-run in full at accept time against the current journey and current inventory (rule 5 excludes
this offer by id only while it is live), and the offer must be `sent`. Order, so that money is
never taken without a seat:

1. `seat.hold` (nothing is charged if the hold fails)
2. `charge` (idempotency key = offer id, so a duplicate tap cannot charge twice)
3. `seat.assign` + `boarding_pass.issue` (any failure from here on VOIDS the charge)
4. confirmation message; offer `accepted`

Any failure leaves the seat unchanged, marks the offer `invalidated` and tells the passenger why.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from acme.customer.closure import close_loop
from acme.customer.skills import CustomerSkills
from acme.domain.journey import OfferRecord
from acme.policy.guardrail_offer import GuardrailDecision, evaluate_traced
from acme.sim.client import ToolError

if TYPE_CHECKING:
    from acme.customer.runtime import CustomerRuntime, JourneyRecord

REASON_TEXT = {
    "disruption_not_resolved": "your journey is being changed",
    "resolution_not_acknowledged": "your trip has not been confirmed yet",
    "open_request_pending": "you have an open request with us",
    "flight_mismatch": "your flight has changed",
    "no_inventory": "the seat is no longer available",
    "not_a_genuine_discount": "the price is no longer valid",
    "price_claim_mismatch": "the price is no longer valid",
    "negative_margin": "the price is no longer valid",
    "tier_not_eligible": "it is no longer open to your booking",
    "offer_already_made": "another offer was already made",
    "not_optional": "it is no longer valid",
    "wrong_origin": "it is no longer valid",
}


class NoSentOffer(LookupError):
    """There is no offer waiting for the passenger's answer."""


def _sent(rec: JourneyRecord) -> OfferRecord | None:
    sent = [r for r in rec.state.offers if r.status == "sent"]
    return sent[-1] if sent else None


async def decline_offer(rt: CustomerRuntime, rec: JourneyRecord) -> str:
    """ "No thanks": mark declined; change nothing else and send nothing (no nudges)."""
    async with rt.journey_lock(rec.state.journey_id):
        record = _sent(rec)
        if record is None:
            raise NoSentOffer
        record.status = "declined"
        rec.state.append_event(
            "passenger", "decline_offer", {"offer_id": str(record.offer.offer_id)}
        )
        await close_loop(rt, rec)
        return "declined"


async def accept_offer(rt: CustomerRuntime, rec: JourneyRecord) -> str:
    """Returns the final offer status: `accepted` or `invalidated`. Raises NoSentOffer."""
    async with rt.journey_lock(rec.state.journey_id):
        record = _sent(rec)
        if record is None:
            if any(r.status == "accepted" for r in rec.state.offers):
                return "accepted"  # duplicate tap after success: idempotent, no new work
            stale = [r for r in rec.state.offers if r.status == "invalidated"]
            if not stale:
                raise NoSentOffer
            # The offer was invalidated (e.g. the journey left RESOLVED): say so honestly.
            return await _convert(rt, rec, stale[-1])
        return await _convert(rt, rec, record)


async def _convert(rt: CustomerRuntime, rec: JourneyRecord, record: OfferRecord) -> str:
    state, offer = rec.state, record.offer
    skills = CustomerSkills(rt)
    state.append_event("passenger", "accept_offer", {"offer_id": str(offer.offer_id)})

    # Time-of-use re-check against the current journey and current inventory.
    inventory = await skills._load_facts(rec, offer.model_dump(mode="json"))  # noqa: SLF001
    state.current_flight = skills._current_flight(rec)  # noqa: SLF001
    decision = evaluate_traced(state, offer, inventory, "accept")
    flight = state.current_flight or ""
    open_premium = rt.world.flights[flight].open_seats.get(inventory.cabin, []) if flight else []
    if record.status != "sent" or not decision.approved or not open_premium:
        # A settled or invalidated offer is never converted (#340), whatever the guardrail says.
        reasons = list(decision.reasons) if not decision.approved else ["no_inventory"]
        if record.status != "sent" and decision.approved:
            reasons = ["offer_already_made"]
        # What is recorded is what happened: a refusal, with its reasons, never the approval the
        # guardrail alone would have given (#354).
        refused = decision if not decision.approved else GuardrailDecision(False, reasons)
        skills._decision_event(state, offer, refused)  # noqa: SLF001
        return await _refuse(rt, rec, record, reasons)
    seat = open_premium[0]
    pnr = rec.pnr_locator
    original = next(
        (
            x["seat"]
            for x in (await rt.client.call("pss", "pnr.get", pnr=pnr))["seats"]
            if x["flight_number"] == flight
        ),
        None,
    )

    try:
        await rt.client.call("dcs", "seat.hold", flight=flight, seat=seat, pnr=pnr)
    except ToolError:
        return await _refuse(rt, rec, record, ["no_inventory"])  # nothing was charged

    try:
        charge = await rt.client.call(
            "payments",
            "charge",
            pnr=pnr,
            amount=f"{offer.price:.2f}",
            currency=offer.currency,
            idempotency_key=str(offer.offer_id),
        )
    except ToolError:
        return await _refuse(rt, rec, record, ["payment_failed"])

    # The approval must still hold AFTER the money moved: a transition out of RESOLVED that
    # raced the charge (anything not holding the journey lock) voids it and changes no seat.
    state.current_flight = skills._current_flight(rec)  # noqa: SLF001
    recheck = evaluate_traced(state, offer, inventory, "recheck")
    if record.status != "sent" or not recheck.approved:
        skills._decision_event(state, offer, recheck)  # noqa: SLF001
        await _void(rt, rec, record, charge["charge_id"], reason="journey_changed")
        return await _refuse(rt, rec, record, list(recheck.reasons), charged=True)

    try:
        await rt.client.call(
            "dcs", "seat.assign", flight=flight, seat=seat, pnr=pnr, cabin=inventory.cabin
        )
        bp = await rt.client.call("dcs", "boarding_pass.issue", pnr=pnr, flight=flight)
    except Exception:  # noqa: BLE001 - ANY failure after the charge must void it
        await _void(rt, rec, record, charge["charge_id"])
        return await _refuse(rt, rec, record, ["upgrade_failed"], charged=True)

    await _release_original_seat(rt, rec, flight, original, seat)
    record.status = "accepted"
    state.append_event(
        "customer_agent",
        "upgrade_confirmed",
        {
            "flight": flight,
            "seat": seat,
            "cabin": inventory.cabin,
            "price": f"{offer.price:.2f}",
            "charge_id": charge["charge_id"],
        },
    )
    await rt.send(
        rec.passenger_id,
        f"You're all set: seat {seat} in premium business on {flight}, ${offer.price:.2f}. "
        "Your new boarding pass is attached.",
        attachments=["boarding_pass"],
    )
    del bp
    await close_loop(rt, rec)
    return "accepted"


async def _release_original_seat(
    rt: CustomerRuntime, rec: JourneyRecord, flight: str, original: str | None, new_seat: str
) -> None:
    """Free the economy seat only after the premium seat is assigned and the pass issued. A
    failure here never undoes the upgrade: it is recorded for a human."""
    if original is None or original == new_seat:
        return
    try:
        await rt.client.call(
            "dcs", "seat.release", flight=flight, seat=original, pnr=rec.pnr_locator
        )
    except ToolError as exc:
        rec.state.append_event(
            "customer_agent", "needs_human", {"step": "seat_release", "error": str(exc)}
        )
        return
    rec.state.append_event(
        "customer_agent",
        "seat_released",
        {"flight": flight, "seat": original, "replaced_by": new_seat},
    )


async def _void(
    rt: CustomerRuntime,
    rec: JourneyRecord,
    record: OfferRecord,
    charge_id: str,
    reason: str = "upgrade_failed",
) -> None:
    offer_id = str(record.offer.offer_id)
    try:
        await rt.client.call("payments", "charge.void", charge_id=charge_id)
        rec.state.append_event(
            "customer_agent",
            "charge_voided",
            {"offer_id": offer_id, "charge_id": charge_id, "reason": reason},
        )
    except Exception:  # noqa: BLE001
        rec.state.append_event(
            "customer_agent",
            "needs_human",
            {"specialist": "payments", "reason": f"void charge {charge_id} failed"},
        )


async def _refuse(
    rt: CustomerRuntime,
    rec: JourneyRecord,
    record: OfferRecord,
    reasons: list[str],
    charged: bool = False,
) -> str:
    record.status = "invalidated"
    if charged:
        changed = next((REASON_TEXT[r] for r in reasons if r in REASON_TEXT), None)
        because = f" because {changed}" if changed else ""
        text = (
            f"I'm sorry, I couldn't complete the upgrade{because}. The payment was reversed, "
            "you have not been charged, and you keep your current seat."
        )
    else:
        why = next((REASON_TEXT[r] for r in reasons if r in REASON_TEXT), None)
        why = why or "it could not be completed"
        text = (
            f"This offer is no longer available because {why}. You have not been charged "
            "and you keep your current seat."
        )
    await rt.send(rec.passenger_id, text)
    return "invalidated"


__all__ = ["NoSentOffer", "accept_offer", "decline_offer"]
