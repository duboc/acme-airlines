"""Layer 3a (claims vs inventory): the guardrail trusts inventory, never the offer's claims."""

from decimal import Decimal

from acme.policy.guardrail_offer import evaluate
from tests.governance.conftest import INVENTORY, make_journey, make_offer


def test_an_inflated_standard_price_is_rejected():
    # Inflating the "was" price makes any discount look bigger than it is.
    d = evaluate(make_journey(), make_offer(standard_price=Decimal("900")), INVENTORY)
    assert not d.approved and "price_claim_mismatch" in d.reasons


def test_a_false_net_margin_claim_is_rejected():
    d = evaluate(make_journey(), make_offer(net_margin=Decimal("500")), INVENTORY)
    assert not d.approved and "price_claim_mismatch" in d.reasons


def test_a_price_below_cost_floor_is_rejected_as_negative_margin():
    offer = make_offer(price=Decimal("10"), net_margin=Decimal("-45"))
    d = evaluate(make_journey(), offer, INVENTORY)
    assert not d.approved and "negative_margin" in d.reasons


def test_zero_inventory_is_rejected():
    empty = INVENTORY.model_copy(update={"open_seats": 0})
    d = evaluate(make_journey(), make_offer(), empty)
    assert not d.approved and "no_inventory" in d.reasons


def test_inventory_for_another_flight_or_cabin_is_rejected():
    other = INVENTORY.model_copy(update={"flight_number": "AC1240"})
    d = evaluate(make_journey(), make_offer(), other)
    assert not d.approved and "no_inventory" in d.reasons


def test_a_price_at_or_above_standard_is_not_a_genuine_discount():
    d = evaluate(
        make_journey(), make_offer(price=Decimal("290"), net_margin=Decimal("235")), INVENTORY
    )
    assert not d.approved and "not_a_genuine_discount" in d.reasons


def test_a_price_above_the_tier_recovery_price_is_rejected():
    d = evaluate(
        make_journey(), make_offer(price=Decimal("200"), net_margin=Decimal("145")), INVENTORY
    )
    assert not d.approved and "not_a_genuine_discount" in d.reasons


def test_the_honest_offer_is_the_only_baseline_that_passes():
    assert evaluate(make_journey(), make_offer(), INVENTORY).approved
