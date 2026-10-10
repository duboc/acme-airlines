"""Scenario 4: inter-agent negotiation and guardrailed upsell (brief S4)."""

from decimal import Decimal

from acme.domain.journey import DisruptionStatus

from .driver import ACCEPT_UPGRADE_BUTTON, EventType, ScenarioDriver
from .flows import ACK, acknowledged, events, offer_delivered, replanned, sole
from .marks import needs


def test_no_offer_before_the_passenger_acknowledges_the_final_resolution(
    driver: ScenarioDriver,
):
    # Given the replanned itinerary is RESOLVED but Mariana has not acknowledged it
    replanned(driver)
    assert driver.get_journey().status is DisruptionStatus.RESOLVED
    # Then no offer exists, none is sent, and Sales never wrote to her
    assert driver.get_journey().offers == []
    assert events(driver, EventType.OFFER_SENT) == []
    assert all(m.offer_id is None and m.sender != "sales" for m in driver.transcript())


@needs(14)
def test_sales_flags_four_open_premium_business_seats_on_ac1252(driver: ScenarioDriver):
    acknowledged(driver)
    opportunity = sole(driver, EventType.SALES_OPPORTUNITY).payload
    assert (opportunity["flight"], opportunity["open_seats"]) == ("AC1252", 4)


@needs(15)
def test_offer_prices_135_against_290_standard_for_80_net_margin(driver: ScenarioDriver):
    offer = offer_delivered(driver)
    assert offer.flight == "AC1252"
    assert offer.cabin == "premium_business"
    assert offer.price == Decimal("135.00")
    assert offer.standard_price == Decimal("290.00")
    assert offer.net_margin == Decimal("80.00")
    assert offer.optional is True


@needs(15)
def test_customer_agent_guardrail_approves_the_offer_as_a_courtesy(driver: ScenarioDriver):
    acknowledged(driver)
    assert sole(driver, EventType.GUARDRAIL_CHECKED).payload["approved"] is True
    assert driver.get_journey().offers[0].status in {"approved", "sent"}


@needs(15)
def test_offer_reaches_the_passenger_only_through_the_customer_agent(driver: ScenarioDriver):
    offer_delivered(driver)
    delivery = [m for m in driver.transcript() if m.offer_id]
    assert len(delivery) == 1
    assert delivery[0].sender == "customer_agent"
    assert ACCEPT_UPGRADE_BUTTON in delivery[0].buttons
    assert all(m.sender != "sales" for m in driver.transcript())


@needs(15)
def test_offer_is_sent_only_after_the_acknowledgement_message(driver: ScenarioDriver):
    offer_delivered(driver)
    transcript = driver.transcript()
    ack_index = next(i for i, m in enumerate(transcript) if m.text == ACK)
    offer_index = next(i for i, m in enumerate(transcript) if m.offer_id)
    assert offer_index > ack_index
