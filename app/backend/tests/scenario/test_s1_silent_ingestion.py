"""Scenario 1: silent ingestion and autonomous profile evaluation (brief S1)."""

from acme.domain.fixtures import DISRUPTION_DETECTED_AT

from .driver import EventType, ScenarioDriver
from .flows import cancelled, sole


def test_cancellation_is_detected_at_2203_from_the_occ_stream(driver: ScenarioDriver):
    # Given Mariana's seeded journey; When OCC cancels AC1234 for maintenance
    cancelled(driver)
    # Then the journey records the detection at 10:03 PM with flight and reason
    detected = sole(driver, EventType.CANCELLATION_DETECTED)
    assert detected.timestamp == DISRUPTION_DETECTED_AT
    assert detected.payload["flight"] == "AC1234"
    assert detected.payload["reason"] == "unscheduled maintenance"


def test_profile_evaluation_loads_diamond_tier_seat_14a_and_one_bag(driver: ScenarioDriver):
    cancelled(driver)
    profile = sole(driver, EventType.PROFILE_LOADED).payload
    assert profile["tier"] == "diamond"
    assert profile["seat"] == "14A"
    assert profile["bags"] == 1


def test_onward_connection_is_flagged_as_the_high_stakes_constraint(driver: ScenarioDriver):
    cancelled(driver)
    connection = sole(driver, EventType.PROFILE_LOADED).payload["connection"]
    assert connection["flight"] == "AC0800"
    assert connection["departure"].startswith("2026-10-10T14:00")


def test_ingestion_is_silent_no_passenger_message_is_sent(driver: ScenarioDriver):
    cancelled(driver)
    assert driver.transcript() == []
