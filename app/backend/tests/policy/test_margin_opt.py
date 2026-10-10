from decimal import Decimal

import pytest

from acme.domain.fixtures import mariana_world
from acme.policy.margin_opt import RECOVERY_DISCOUNT_PCT, recovery_quote


def inv(**over):
    base = mariana_world().inventory[0]
    return base.model_copy(update=over)


def test_diamond_fixture_is_135_over_290_with_80_margin():
    q = recovery_quote(inv(), "diamond")
    assert (q.price, q.standard_price, q.net_margin) == (
        Decimal("135.00"),
        Decimal("290.00"),
        Decimal("80.00"),
    )


@pytest.mark.parametrize("tier", ["basic", "silver"])
def test_ineligible_tiers_get_no_quote(tier):
    assert recovery_quote(inv(), tier) is None


def test_gold_is_eligible_with_smaller_discount():
    q = recovery_quote(inv(), "gold")
    assert q.price == Decimal("175.00")
    assert RECOVERY_DISCOUNT_PCT["gold"] < RECOVERY_DISCOUNT_PCT["diamond"]


def test_price_never_below_cost_floor():
    q = recovery_quote(
        inv(standard_upgrade_fare=Decimal("100"), cost_floor=Decimal("90")), "diamond"
    )
    assert q.price == Decimal("90.00")
    assert q.net_margin == Decimal("0.00")


def test_price_never_above_standard():
    q = recovery_quote(inv(standard_upgrade_fare=Decimal("2"), cost_floor=Decimal("0")), "gold")
    assert q.price <= Decimal("2")
