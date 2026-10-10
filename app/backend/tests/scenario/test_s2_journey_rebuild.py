"""Scenario 2: zero-touch backstage journey rebuilding (brief S2)."""

from acme.domain.fixtures import PARTNER_HOTEL_ID
from acme.domain.journey import DisruptionStatus

from .driver import EventType, ScenarioDriver
from .flows import AC1240_DEP, cancelled, events, sole
from .marks import needs


@needs(11)
def test_rebooked_on_ac1240_nonstop_at_0815(driver: ScenarioDriver):
    cancelled(driver)
    rebooked = sole(driver, EventType.REBOOKED).payload
    assert rebooked["flight"] == "AC1240"
    assert rebooked["departure"] == AC1240_DEP.isoformat()


@needs(11)
def test_seat_14a_is_held_on_the_new_aircraft(driver: ScenarioDriver):
    cancelled(driver)
    held = sole(driver, EventType.SEAT_HELD).payload
    assert (held["flight"], held["seat"]) == ("AC1240", "14A")


@needs(11)
def test_checked_bag_is_retagged_and_routed_to_ac1240(driver: ScenarioDriver):
    cancelled(driver)
    bag = sole(driver, EventType.BAG_REROUTED).payload
    assert bag["flight"] == "AC1240"
    assert bag["tag"] == "BAG0001"


@needs(11)
def test_santiago_connection_is_preserved_by_the_rebooking(driver: ScenarioDriver):
    cancelled(driver)
    check = sole(driver, EventType.CONNECTION_VERIFIED).payload
    assert (check["flight"], check["connection_flight"]) == ("AC1240", "AC0800")


@needs(12)
def test_duty_of_care_books_ht8842_a_transfer_and_a_dinner_voucher(driver: ScenarioDriver):
    cancelled(driver)
    assert sole(driver, EventType.HOTEL_BOOKED).payload["hotel_id"] == PARTNER_HOTEL_ID
    assert len(events(driver, EventType.TRANSFER_ARRANGED)) >= 1
    kinds = {e.payload["kind"] for e in events(driver, EventType.VOUCHER_ISSUED)}
    assert "meal" in kinds


@needs(12)
def test_journey_reaches_resolved_after_the_rebuild(driver: ScenarioDriver):
    cancelled(driver)
    assert driver.get_journey().status is DisruptionStatus.RESOLVED


def test_sales_is_suppressed_while_the_disruption_is_being_resolved(driver: ScenarioDriver):
    # Sales watches Premium Business inventory but may not pitch during resolution.
    cancelled(driver)
    assert driver.get_journey().offers == []
    assert [m for m in driver.transcript() if m.sender == "sales" or m.offer_id] == []
