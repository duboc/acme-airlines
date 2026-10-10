"""Typed timeline events: discriminated union in the OpenAPI contract (#61)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from acme.api.events import EVENT_TYPES, TimelineEvent
from acme.customer.runtime import CustomerRuntime
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "openapi.json").read_text())
SCHEMAS = CONTRACT["components"]["schemas"]
ADAPTER = TypeAdapter(TimelineEvent)
NOW = datetime(2026, 10, 9, 22, 3, tzinfo=UTC).isoformat()


def event(type_, payload, actor="x"):
    return {"actor": actor, "type": type_, "payload": payload, "timestamp": NOW}


def test_contract_exposes_discriminated_union_over_all_event_types():
    events_schema = CONTRACT["paths"]["/journeys/{journey_id}/events"]["get"]["responses"]["200"]
    item = events_schema["content"]["application/json"]["schema"]["items"]
    assert item["discriminator"]["propertyName"] == "type"
    assert set(item["discriminator"]["mapping"]) == set(EVENT_TYPES)
    assert {
        "guardrail_decision",
        "sales_suppressed",
        "cancellation_detected",
        "profile_loaded",
    } <= (set(EVENT_TYPES))


def test_guardrail_decision_schema():
    payload = SCHEMAS["GuardrailDecisionPayload"]
    assert set(payload["required"]) == {"approved", "reasons", "offer_id"}
    assert payload["properties"]["approved"]["type"] == "boolean"
    assert payload["properties"]["reasons"]["items"]["type"] == "string"


def test_sales_suppressed_schema():
    payload = SCHEMAS["SalesSuppressedPayload"]
    assert set(payload["required"]) == {"journey_status", "reason"}


def test_valid_and_invalid_payloads():
    ok = ADAPTER.validate_python(
        event("guardrail_decision", {"approved": False, "reasons": ["not_acknowledged"],
                                     "offer_id": None}, "guardrail")
    )  # fmt: skip
    assert ok.payload.approved is False
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(event("guardrail_decision", {"approved": "yes", "reasons": []}))
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(event("no_such_event", {}))
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(event("cancellation_detected", {"flight": "AC1234", "extra": 1}))


async def test_every_event_the_runtime_emits_validates_against_the_contract():
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT))
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    timeline = rt.journeys[jid].state.timeline
    assert len(timeline) >= 4
    for e in timeline:
        ADAPTER.validate_python(e.model_dump(mode="json"))
    assert {e.type for e in timeline} <= set(EVENT_TYPES)


SAMPLES = {
    "status_changed": {"from": "DETECTED", "to": "RESOLVING"},
    "rebooked": {"flight": "AC1240", "departure": NOW, "arrival": NOW},
    "seat_held": {"flight": "AC1240", "seat": "14A", "requested": "14A", "reason": "kept"},
    "bag_rerouted": {"flight": "AC1240", "tag": "BAG0001"},
    "connection_verified": {"flight": "AC1240", "connection_flight": "AC0800",
                            "feasible": True, "margin_minutes": 135},
    "needs_human": {"specialist": "seat", "reason": "none open"},
    "duty_of_care_assessed": {
        "communication_included": True, "meal_vouchers": ["dinner"], "hotel_required": True,
        "ground_transfer_required": True, "reaccommodation_required": True,
        "compensation_eligible": True, "rule_reasons": {"hotel": "ANAC 400 Art. 27, III"},
    },
    "duty_of_care_fulfilled": {},
    "hotel_booked": {"booking_ref": "H1", "hotel_id": "HT8842", "entitlement": "hotel",
                     "reason": "r"},
    "transfer_arranged": {"transfer_ref": "T1", "leg": "to_hotel", "entitlement": "t",
                          "reason": "r"},
    "notification_sent": {"message_id": "WM1", "source": "template"},
    "resolution_acknowledged": {},
    "change_handoff": {"request_id": "r1", "after": "10:00"},
    "request_closed": {"request_id": "r1", "outcome": "unsatisfiable"},
    "voucher_issued": {"voucher_id": "V1", "kind": "meal", "meal_type": "dinner", "amount": "25.00",
                       "entitlement": "meals", "reason": "r"},
    "resolution_blocked": {"missing": ["seat"]},
    "passenger_message": {"text": "hi"},
    "accept_offer": {"offer_id": "o1"},
    "decline_offer": {"offer_id": "o1"},
    "guardrail_decision": {"approved": True, "reasons": [], "offer_id": "o1"},
    "sales_suppressed": {"journey_status": "RESOLVING", "reason": "disruption_not_resolved"},
    "offer_proposed": {"offer_id": "o1", "flight": "AC1252"},
    "sales_opportunity": {"flight": "AC1252", "open_seats": 4},
    "offer_sent": {"offer_id": "o1", "message_id": "m1"},
    "upgrade_confirmed": {"flight": "AC1252", "seat": "2A", "cabin": "premium_business",
                          "price": "135.00"},
    "verification_passed": {"checks": {"flight": True}},
    "memory_committed": {"anything": 1},
    "crm_logged": {},
    "nps_scheduled": {},
    "analytics_recorded": {"converted": True},
}  # fmt: skip


@pytest.mark.parametrize("type_", EVENT_TYPES)
def test_each_event_type_has_a_validating_sample(type_):
    if type_ in ("cancellation_detected", "profile_loaded", "connection_constraint"):
        pytest.skip("covered by the runtime test")
    assert type_ in SAMPLES, f"add a sample payload for {type_}"
    ADAPTER.validate_python(event(type_, SAMPLES[type_]))


def test_voucher_requires_kind_and_rejects_the_retired_meal_key():
    base = {"voucher_id": "V1", "amount": "25.00", "entitlement": "meals", "reason": "r"}
    assert ADAPTER.validate_python(event("voucher_issued", {**base, "kind": "meal"}))
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(event("voucher_issued", {**base, "meal": "dinner"}))


def test_retired_transfer_booked_event_type_is_rejected():
    payload = {"transfer_ref": "T1", "leg": "x", "entitlement": "t", "reason": "r"}
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(event("transfer_booked", payload))
