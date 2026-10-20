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


@pytest.mark.parametrize(
    "over",
    [
        {"open_seats": 0},
        {"standard_upgrade_fare": Decimal("290.00"), "cost_floor": Decimal("290.00")},
        {"standard_upgrade_fare": Decimal("290.00"), "cost_floor": Decimal("310.00")},
    ],
)
def test_no_quote_when_no_open_seats_or_cost_floor_meets_or_exceeds_standard(over):
    assert recovery_quote(inv(**over), "diamond") is None
    assert recovery_quote(inv(**over), "gold") is None


def test_open_seats_boundary_one_yields_quote_and_zero_yields_none():
    q = recovery_quote(inv(open_seats=1), "diamond")
    assert q is not None
    assert q.price == Decimal("135.00")
    assert recovery_quote(inv(open_seats=0), "diamond") is None


def test_ceil_to_5_boundary_conditions():
    def gold(fare):
        return recovery_quote(
            inv(standard_upgrade_fare=Decimal(fare), cost_floor=Decimal("55"), open_seats=1),
            "gold",
        )

    exact = gold("300.00")  # 300 * 0.60 = 180.00, an exact $5 multiple: unchanged
    assert exact.price == Decimal("180.00")
    assert exact.net_margin == Decimal("125.00")
    just_above = gold("300.02")  # 180.012 -> next $5 point
    assert just_above.price == Decimal("185.00")
    assert just_above.net_margin == Decimal("130.00")
