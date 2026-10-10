"""Typed timeline events: a discriminated union by `type` for the OpenAPI contract (#61).

`JourneyState.timeline` stays generic (`JourneyEvent`, free-form payload). This module is the
contract view of it: every event type the agents emit has an explicit payload schema, so UI and
tests no longer guess payload shapes. The drift test validates real runtime events against it.

Payloads are strict (`extra="forbid"`) for event types that are emitted today. Event types that
later issues (#14-#16) will emit are `Provisional` (extra keys allowed) until they land; tighten
them in the PR that emits them.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, create_model


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provisional(BaseModel):
    """Payload not emitted yet; shape is the documented intent, extra keys tolerated."""

    model_config = ConfigDict(extra="allow")


class Empty(Payload):
    pass


class StatusChangedPayload(Payload):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from")
    to: str


class CancellationDetectedPayload(Payload):
    flight: str
    reason: str


class ConnectionRef(Payload):
    flight: str
    departure: AwareDatetime


class ProfileLoadedPayload(Payload):
    tier: str
    seat: str | None
    bags: int
    connection: ConnectionRef | None


class ConnectionConstraintPayload(Payload):
    airport: str
    mct_minutes: int
    latest_arrival: AwareDatetime
    text: str


class RebookedPayload(Payload):
    flight: str
    departure: AwareDatetime
    arrival: AwareDatetime


class SeatHeldPayload(Payload):
    flight: str
    seat: str
    requested: str
    reason: str


class BagReroutedPayload(Payload):
    flight: str
    tag: str


class ConnectionVerifiedPayload(Payload):
    flight: str
    connection_flight: str
    feasible: bool
    margin_minutes: int


class NeedsHumanPayload(Payload):
    """Either a specialist failure (`specialist`, `reason`) or a tool failure (`step`, `error`)."""

    specialist: str | None = None
    reason: str | None = None
    step: str | None = None
    error: str | None = None


class DutyOfCareAssessedPayload(Payload):
    communication_included: bool
    meal_vouchers: list[str]
    hotel_required: bool
    ground_transfer_required: bool
    reaccommodation_required: bool
    compensation_eligible: bool
    rule_reasons: dict[str, str]


class HotelBookedPayload(Payload):
    booking_ref: str
    hotel_id: str
    entitlement: str
    reason: str


class TransferArrangedPayload(Payload):
    transfer_ref: str
    leg: str
    entitlement: str
    reason: str


class VoucherIssuedPayload(Payload):
    voucher_id: str
    kind: str
    meal_type: str | None = None
    amount: str
    entitlement: str
    reason: str


class NotificationSentPayload(Payload):
    message_id: str
    source: Literal["llm", "template"]


class ChangeHandoffPayload(Payload):
    request_id: str
    after: str


class RequestClosedPayload(Payload):
    request_id: str
    outcome: Literal["satisfied", "unsatisfiable"]


class ResolutionBlockedPayload(Payload):
    missing: list[str]


class PassengerMessagePayload(Payload):
    text: str


class OfferActionPayload(Payload):
    offer_id: str


class GuardrailDecisionPayload(Payload):
    approved: bool
    reasons: list[str]
    offer_id: str | None


class SalesSuppressedPayload(Payload):
    journey_status: str
    reason: str


class OfferProposedPayload(Payload):
    offer_id: str
    flight: str


class SalesOpportunityPayload(Provisional):
    flight: str
    open_seats: int


class OfferSentPayload(Payload):
    offer_id: str
    message_id: str


class UpgradeConfirmedPayload(Provisional):
    flight: str
    seat: str
    cabin: str
    price: str


class VerificationPassedPayload(Provisional):
    checks: dict[str, Any]


class ConversionPayload(Provisional):
    converted: bool


class AnyProvisional(Provisional):
    pass


PAYLOADS: dict[str, type[BaseModel]] = {
    "status_changed": StatusChangedPayload,
    "cancellation_detected": CancellationDetectedPayload,
    "profile_loaded": ProfileLoadedPayload,
    "connection_constraint": ConnectionConstraintPayload,
    "rebooked": RebookedPayload,
    "seat_held": SeatHeldPayload,
    "bag_rerouted": BagReroutedPayload,
    "connection_verified": ConnectionVerifiedPayload,
    "needs_human": NeedsHumanPayload,
    "duty_of_care_assessed": DutyOfCareAssessedPayload,
    "duty_of_care_fulfilled": Empty,
    "hotel_booked": HotelBookedPayload,
    "transfer_arranged": TransferArrangedPayload,
    "notification_sent": NotificationSentPayload,
    "resolution_acknowledged": Empty,
    "change_handoff": ChangeHandoffPayload,
    "request_closed": RequestClosedPayload,
    "voucher_issued": VoucherIssuedPayload,
    "resolution_blocked": ResolutionBlockedPayload,
    "passenger_message": PassengerMessagePayload,
    "accept_offer": OfferActionPayload,
    "decline_offer": OfferActionPayload,
    "guardrail_decision": GuardrailDecisionPayload,
    "sales_suppressed": SalesSuppressedPayload,
    "offer_proposed": OfferProposedPayload,  # Sales proposal received (#14)
    # Provisional: emitted by #16.
    "sales_opportunity": SalesOpportunityPayload,
    "offer_sent": OfferSentPayload,
    "upgrade_confirmed": UpgradeConfirmedPayload,
    "verification_passed": VerificationPassedPayload,
    "memory_committed": AnyProvisional,
    "crm_logged": AnyProvisional,
    "nps_scheduled": AnyProvisional,
    "analytics_recorded": ConversionPayload,
}
EVENT_TYPES = tuple(PAYLOADS)


class _EventBase(BaseModel):
    actor: str
    timestamp: AwareDatetime


def _event_model(type_: str, payload: type[BaseModel]) -> type[_EventBase]:
    name = "".join(p.capitalize() for p in type_.split("_")) + "Event"
    return create_model(  # type: ignore[call-overload]
        name, __base__=_EventBase, type=(Literal[type_], ...), payload=(payload, ...)
    )


_MODELS = tuple(_event_model(t, p) for t, p in PAYLOADS.items())
TimelineEvent = Annotated[Union[_MODELS], Field(discriminator="type")]  # noqa: UP007
