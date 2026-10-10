"""Scenario 3: proactive notification and conversational replanning (brief S3)."""

from datetime import timedelta

from acme.domain.journey import DisruptionStatus

from .driver import EventType, ScenarioDriver
from .flows import AC1252_DEP, NEW_CONSTRAINT, NOTIFIED_AT, notified, replanned


def test_whatsapp_message_is_sent_at_2209(driver: ScenarioDriver):
    notified(driver)
    first = driver.transcript()[0]
    assert first.sender == "customer_agent"
    assert first.timestamp == NOTIFIED_AT


def test_message_states_cancellation_and_the_completed_resolution(driver: ScenarioDriver):
    notified(driver)
    text = driver.transcript()[0].text
    for fact in ("AC1234", "AC1240", "08:15", "14A", "HT8842"):
        assert fact in text, f"{fact} missing from notification"


def test_message_attaches_the_dinner_voucher_and_has_no_offer(driver: ScenarioDriver):
    notified(driver)
    first = driver.transcript()[0]
    assert "meal_voucher" in first.attachments
    assert first.offer_id is None


def test_after_10am_request_moves_the_passenger_to_ac1252_at_1140(driver: ScenarioDriver):
    replanned(driver)
    assert driver.transcript()[1].text == NEW_CONSTRAINT
    latest = [e for e in driver.get_journey().timeline if e.type == EventType.REBOOKED][-1]
    assert latest.payload["flight"] == "AC1252"
    assert latest.payload["departure"] == AC1252_DEP.isoformat()


def test_replanning_keeps_seat_14a_and_reverifies_the_connection(driver: ScenarioDriver):
    replanned(driver)
    seat = [e for e in driver.get_journey().timeline if e.type == EventType.SEAT_HELD][-1]
    assert (seat.payload["flight"], seat.payload["seat"]) == ("AC1252", "14A")
    check = [e for e in driver.get_journey().timeline if e.type == EventType.CONNECTION_VERIFIED]
    assert (check[-1].payload["flight"], check[-1].payload["connection_flight"]) == (
        "AC1252",
        "AC0800",
    )


def test_replanning_completes_in_seconds_and_returns_to_resolved(driver: ScenarioDriver):
    replanned(driver)
    reply_at = driver.transcript()[1].timestamp
    rebooked_at = [e for e in driver.get_journey().timeline if e.type == EventType.REBOOKED][-1]
    assert rebooked_at.timestamp - reply_at <= timedelta(seconds=30)
    assert driver.get_journey().status is DisruptionStatus.RESOLVED
