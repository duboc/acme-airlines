"""Rights & Compensation specialist: runs `duty_of_care` and records entitlements + reasons."""

from __future__ import annotations

from datetime import datetime

from acme.policy.duty_of_care import (
    DisruptionReason,
    DutyOfCareEntitlements,
    evaluate_duty_of_care,
)

_FORCE_MAJEURE_WORDS = ("weather", "storm", "fog", "volcan", "strike", "security", "air traffic")


def classify_reason(reason: str) -> DisruptionReason:
    lowered = reason.lower()
    return (
        "force_majeure"
        if any(w in lowered for w in _FORCE_MAJEURE_WORDS)
        else ("technical_maintenance")
    )


def assess(
    *,
    origin: str,
    destination: str,
    reason: str,
    detected_at: datetime,
    original_departure: datetime,
    new_departure: datetime,
    resident: bool = False,
    special_needs: bool = False,
) -> DutyOfCareEntitlements:
    """The wait runs from `min(detected_at, original_departure)` (local) to the new departure.

    Overnight = the new departure falls on a later local calendar day than the wait start.
    """
    tz = original_departure.tzinfo
    wait_begin = min(detected_at, original_departure).astimezone(tz)
    new_local = new_departure.astimezone(tz)
    return evaluate_duty_of_care(
        origin_airport=origin,
        destination_airport=destination,
        disruption_type="cancellation",
        disruption_reason=classify_reason(reason),
        wait_hours=max((new_local - wait_begin).total_seconds() / 3600, 0),
        wait_start=wait_begin.time(),
        requires_overnight=new_local.date() > wait_begin.date(),
        passenger_is_origin_resident=resident,
        passenger_has_special_needs=special_needs,
    )


def entitlements_payload(e: DutyOfCareEntitlements) -> dict:
    return {
        "communication_included": e.communication_included,
        "meal_vouchers": e.meal_vouchers,
        "hotel_required": e.hotel_required,
        "ground_transfer_required": e.ground_transfer_required,
        "reaccommodation_required": e.reaccommodation_required,
        "compensation_eligible": e.compensation_eligible,
        "rule_reasons": e.rule_reasons,
    }
