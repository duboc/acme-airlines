"""Duty of care, outreach and verification follow DOC-05..08, not a fixed hotel + voucher (#324)."""

from datetime import timedelta

from acme.customer.outreach import (
    collect_facts,
    notification_due,
    prepare_notification,
    send_notification,
)
from acme.customer.runtime import CustomerRuntime
from acme.customer.specialists import RebuildResult, run_duty_of_care
from acme.customer.verify import verify_journey
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus as S
from acme.llm.fake import FakeLlm

DONE = RebuildResult(new_flight="AC1240", seat_done=True, bag_done=True)


def events(rec, type_):
    return [e for e in rec.state.timeline if e.type == type_]


async def case(*, resident=False, special_needs=False, wait_hours=None):
    """Mariana's cancellation, optionally a resident / PNAE passenger and a same-day wait."""
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm(), auto_duty_of_care=False)
    p = rt.world.passengers["P-MARIANA"]
    p.origin_resident, p.special_needs = resident, special_needs
    if wait_hours is not None:  # a same-day wait: no overnight
        flights = rt.world.flights
        original = flights["AC1234"].scheduled_departure.replace(hour=10, minute=0)
        flights["AC1234"].scheduled_departure = original
        flights["AC1240"].scheduled_departure = original + timedelta(hours=wait_hours)
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    rec = rt.journeys[jid]
    await run_duty_of_care(rt, rec, DONE)
    return rt, rec


async def notified(rt, rec):
    """The proactive message, sent once it is due."""
    rec.state.resolution_acknowledged = False
    rt.clock.advance(rt.notify_delay + timedelta(minutes=1))
    assert notification_due(rt, rec)
    prepared = await prepare_notification(rt, rec)
    assert prepared is not None
    await send_notification(rt, rec, prepared)
    (msg,) = [m for m in rt.messages[rec.passenger_id] if m.direction == "outbound"]
    return prepared, msg


async def test_ac4_doc05_default_is_unchanged():
    rt, rec = await case()
    assert events(rec, "duty_of_care_assessed")[0].payload["rule_reasons"]["hotel"]
    assert rec.state.status is S.RESOLVED and rec.hotel is not None
    assert [e.payload["leg"] for e in events(rec, "transfer_arranged")] == [
        "airport_to_hotel",
        "hotel_to_airport",
    ]
    prepared, msg = await notified(rt, rec)
    assert list(msg.attachments) == ["hotel_voucher", "meal_voucher"]
    assert "hotel" in prepared.text and "transfer" in prepared.text
    assert (await verify_journey(rt, rec)).checks["hotel"] is True


async def test_ac1_doc06_resident_gets_a_transfer_home_and_no_hotel():
    rt, rec = await case(resident=True)
    p = events(rec, "duty_of_care_assessed")[0].payload
    assert p["hotel_required"] is False and p["ground_transfer_required"] is True
    assert rec.hotel is None and not events(rec, "hotel_booked")
    assert [e.payload["leg"] for e in events(rec, "transfer_arranged")] == ["airport_to_home"]
    assert rec.state.status is S.RESOLVED and not events(rec, "needs_human")


async def test_ac2_doc06_message_has_no_hotel_and_attaches_only_the_meal_voucher():
    rt, rec = await case(resident=True)
    facts = collect_facts(rec)
    assert facts is not None and facts["hotel_id"] is None and facts["has_transfer"]
    prepared, msg = await notified(rt, rec)
    assert list(msg.attachments) == ["meal_voucher"]
    assert "hotel" not in prepared.text.lower()


async def test_ac3_doc06_passes_the_closed_loop_without_a_hotel():
    rt, rec = await case(resident=True)
    res = await verify_journey(rt, rec)
    assert res.checks["hotel"] is True and res.ok, res.details


async def test_doc06_resident_and_special_needs_overnight_flow():
    rt, rec = await case(resident=True, special_needs=True)
    p = events(rec, "duty_of_care_assessed")[0].payload
    assert p["hotel_required"] is False and p["ground_transfer_required"] is True
    assert p["rule_reasons"]["hotel"] == "ANAC 400 Art. 27, § 1º"
    assert rec.hotel is None and not events(rec, "hotel_booked")
    assert [e.payload["leg"] for e in events(rec, "transfer_arranged")] == ["airport_to_home"]
    assert rec.state.status is S.RESOLVED and not events(rec, "needs_human")


async def test_doc07_pnae_daytime_wait_gets_hotel_and_transfer():
    rt, rec = await case(special_needs=True, wait_hours=6)
    p = events(rec, "duty_of_care_assessed")[0].payload
    assert p["hotel_required"] and p["ground_transfer_required"]
    assert rec.hotel is not None and len(events(rec, "transfer_arranged")) == 2
    assert rec.state.status is S.RESOLVED


async def test_doc08_short_daytime_wait_has_no_hotel_transfer_and_still_closes():
    rt, rec = await case(wait_hours=2)
    p = events(rec, "duty_of_care_assessed")[0].payload
    assert not p["hotel_required"] and not p["ground_transfer_required"]
    assert rec.hotel is None and not events(rec, "transfer_arranged")
    assert rec.state.status is S.RESOLVED and not events(rec, "needs_human")
    facts = collect_facts(rec)
    assert facts is not None and not facts["has_transfer"]
    prepared, msg = await notified(rt, rec)
    assert "hotel" not in prepared.text.lower() and "transfer" not in prepared.text.lower()
    assert set(msg.attachments) <= {"meal_voucher"}
    res = await verify_journey(rt, rec)
    assert res.checks["hotel"] is True and res.ok, res.details


async def test_a_hotel_that_was_due_but_never_booked_still_fails_verification():
    rt, rec = await case()
    rec.hotel = None
    rec.state._timeline[:] = [e for e in rec.state.timeline if e.type != "hotel_booked"]  # noqa: SLF001
    assert collect_facts(rec) is None  # DOC-05 care incomplete: nothing is announced
    assert (await verify_journey(rt, rec)).checks["hotel"] is False


async def test_missing_assessment_keeps_the_full_doc05_requirements():
    rt, rec = await case()
    rec.state._timeline[:] = [e for e in rec.state.timeline if e.type != "duty_of_care_assessed"]  # noqa: SLF001
    assert collect_facts(rec) is not None
    rec.state._timeline[:] = [e for e in rec.state.timeline if e.type != "voucher_issued"]  # noqa: SLF001
    assert collect_facts(rec) is None
