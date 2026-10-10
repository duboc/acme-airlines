"""Scenario 5: frictionless conversion and closed-loop verification (brief S5)."""

from decimal import Decimal

from acme.domain.journey import DisruptionStatus

from .driver import DECLINE_UPGRADE_BUTTON, EventType, ScenarioDriver
from .flows import accepted, events, offer_delivered, sole


def test_boarding_pass_and_hotel_voucher_are_delivered_over_whatsapp(driver: ScenarioDriver):
    offer_delivered(driver)
    attachments = {a for m in driver.transcript() for a in m.attachments}
    assert {"boarding_pass", "hotel_voucher"} <= attachments


def test_one_tap_acceptance_confirms_seat_2a_premium_business_for_135(driver: ScenarioDriver):
    accepted(driver)
    upgrade = sole(driver, EventType.UPGRADE_CONFIRMED).payload
    assert (upgrade["flight"], upgrade["seat"], upgrade["cabin"]) == (
        "AC1252",
        "2A",
        "premium_business",
    )
    assert Decimal(str(upgrade["price"])) == Decimal("135")
    assert driver.get_journey().offers[0].status == "accepted"


def test_verification_finds_flight_seat_hotel_baggage_and_upgrade_in_sync(
    driver: ScenarioDriver,
):
    accepted(driver)
    checks = sole(driver, EventType.VERIFICATION_PASSED).payload["checks"]
    assert {"flight", "seat", "hotel", "baggage", "upgrade"} <= set(checks)


def test_journey_state_is_committed_to_memory_and_crm_nps_analytics_are_recorded(
    driver: ScenarioDriver,
):
    accepted(driver)
    assert len(events(driver, EventType.MEMORY_COMMITTED)) == 1
    assert len(events(driver, EventType.CRM_LOGGED)) == 1
    assert len(events(driver, EventType.NPS_SCHEDULED)) == 1
    assert sole(driver, EventType.ANALYTICS_RECORDED).payload["converted"] is True


def test_verification_does_not_run_before_the_passenger_decides(driver: ScenarioDriver):
    offer_delivered(driver)
    assert events(driver, EventType.VERIFICATION_PASSED) == []


def test_declining_keeps_seat_14a_closes_the_journey_without_a_charge(driver: ScenarioDriver):
    offer_delivered(driver)
    driver.passenger_tap(DECLINE_UPGRADE_BUTTON)
    journey = driver.get_journey()
    assert journey.offers[0].status == "declined"
    assert events(driver, EventType.UPGRADE_CONFIRMED) == []
    assert sole(driver, EventType.ANALYTICS_RECORDED).payload["converted"] is False
    assert len(events(driver, EventType.VERIFICATION_PASSED)) == 1
    assert journey.status is DisruptionStatus.CLOSED
