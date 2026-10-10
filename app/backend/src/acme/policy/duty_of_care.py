"""Duty of care for a disruption departing Brazil. Pure, deterministic, no I/O, no LLM.

Primary regime: ANAC Resolution 400/2016 (flights departing Brazilian airports). Rule table
and citations: docs/product/policy-rules.md, derived from docs/research/duty-of-care.md (#31).
This is a simplified demo table, not legal advice.

Rules DOC-01..DOC-11 (communication >=1h, meals >=2h by time-of-day window, hotel+transfer
>=4h with overnight, resident and PNAE carve-outs, re-accommodation, compensation flag):
see the table in docs/product/policy-rules.md. Citations are returned per entitlement in
`rule_reasons`.

Care (DOC-01..09) is never suspended by force majeure (ANAC 400 Art. 26, § 3º). EU261 is
not applied on a third-country route (EU261 Art. 3(1)); routes touching an EU airport are
flagged for review rather than silently ignored.
"""

from dataclasses import dataclass, field
from datetime import time
from typing import Literal

DisruptionType = Literal["cancellation", "delay"]
DisruptionReason = Literal["technical_maintenance", "force_majeure"]

BRAZILIAN_AIRPORTS = frozenset({"GRU", "CGH", "VCP", "GIG", "SDU", "BSB", "CNF", "CWB", "POA"})
EU_AIRPORTS = frozenset({"MAD", "BCN", "LIS", "CDG", "FRA", "AMS", "FCO", "MUC"})

ART27 = "ANAC 400 Art. 27"
NOT_MET = "(threshold not met)"


@dataclass(frozen=True)
class DutyOfCareEntitlements:
    communication_included: bool
    meal_vouchers: list[str]
    hotel_required: bool
    ground_transfer_required: bool
    reaccommodation_required: bool
    compensation_eligible: bool
    rule_reasons: dict[str, str] = field(default_factory=dict)


def _meals(wait_start: time, wait_hours: float, overnight: bool) -> list[str]:
    if wait_hours < 2.0:
        return []
    meals: list[str] = []
    if time(11) <= wait_start < time(15):
        meals.append("lunch")
    elif time(18) <= wait_start < time(23):
        meals.append("dinner")
    if overnight and wait_hours >= 4.0:
        meals.append("breakfast")
    return meals


def evaluate_duty_of_care(
    *,
    origin_airport: str,
    destination_airport: str,
    disruption_type: DisruptionType,
    disruption_reason: DisruptionReason,
    wait_hours: float,
    wait_start: time,
    requires_overnight: bool,
    passenger_is_origin_resident: bool,
    passenger_has_special_needs: bool,
) -> DutyOfCareEntitlements:
    """`wait_start` is the local time the passenger's wait begins (e.g. the cancellation time)."""
    if origin_airport not in BRAZILIAN_AIRPORTS:
        raise ValueError("only departures from Brazil (ANAC 400) are supported")
    if wait_hours < 0:
        raise ValueError("wait_hours must be >= 0")

    long_wait = wait_hours >= 4.0
    meals = _meals(wait_start, wait_hours, requires_overnight)

    if long_wait and requires_overnight and passenger_is_origin_resident:
        hotel, transfer, lodging = False, True, f"{ART27}, § 1º"
    elif long_wait and requires_overnight:
        hotel, transfer, lodging = True, True, f"{ART27}, III"
    elif long_wait and passenger_has_special_needs:
        hotel, transfer, lodging = True, True, f"{ART27}, § 2º"
    elif long_wait:
        hotel, transfer, lodging = False, False, f"{ART27}, III (no overnight stay required)"
    else:
        hotel, transfer, lodging = False, False, f"{ART27}, III {NOT_MET}"

    compensable = disruption_reason == "technical_maintenance" and long_wait
    if disruption_reason == "force_majeure":
        compensation = "CDC Art. 14, § 3º, II (excludente de responsabilidade)"
    elif compensable:
        compensation = "CDC Art. 14 / STJ REsp 1.584.465 (fortuito interno)"
    else:
        compensation = f"CDC Art. 14 {NOT_MET}"

    if meals:
        meal_reason = f"{ART27}, II"
    elif wait_hours >= 2.0:
        meal_reason = f"{ART27}, II (no meal window)"
    else:
        meal_reason = f"{ART27}, II {NOT_MET}"

    if EU_AIRPORTS & {origin_airport, destination_airport}:
        eu = "EU261 may apply: route touches an EU airport (Art. 3(1)); refer for review"
    else:
        eu = "EU261 non-applicable: third-country route (Art. 3(1))"

    reaccommodate = disruption_type == "cancellation" or long_wait
    return DutyOfCareEntitlements(
        communication_included=wait_hours >= 1.0,
        meal_vouchers=meals,
        hotel_required=hotel,
        ground_transfer_required=transfer,
        reaccommodation_required=reaccommodate,
        compensation_eligible=compensable,
        rule_reasons={
            "communication": f"{ART27}, I" + ("" if wait_hours >= 1.0 else f" {NOT_MET}"),
            "meals": meal_reason,
            "hotel": lodging,
            "ground_transfer": lodging,
            "reaccommodation": "ANAC 400 Art. 21, I & Art. 28"
            if reaccommodate
            else f"ANAC 400 Art. 21 {NOT_MET}",
            "compensation": compensation,
            "eu261": eu,
        },
    )
