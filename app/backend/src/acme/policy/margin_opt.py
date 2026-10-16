"""Recovery-price optimisation for the Premium Business upgrade courtesy.

Pure and deterministic; no LLM involved.

Formula (all amounts USD, `Decimal`):

    discounted = standard_fare * (1 - discount_pct[tier] / 100)
    price      = ceil_to_5(discounted)            # whole-$5 price points
    price      = clamp(price, cost_floor, standard_fare)
    net_margin = price - cost_floor

Tier table (`RECOVERY_DISCOUNT_PCT`): which tiers get a recovery courtesy price.

    diamond  55 % off     (fixture: 290 -> 130.50 -> 135, net +80 over the 55 floor)
    gold     40 % off
    silver   not eligible
    basic    not eligible
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from pydantic import BaseModel

from acme.domain.models import CabinInventory, Tier

RECOVERY_DISCOUNT_PCT: dict[Tier, Decimal] = {
    "diamond": Decimal("55"),
    "gold": Decimal("40"),
}
_CENT = Decimal("0.01")
_STEP = Decimal("5")


class PriceQuote(BaseModel):
    price: Decimal
    standard_price: Decimal
    net_margin: Decimal


def recovery_quote(inventory: CabinInventory, tier: Tier | None) -> PriceQuote | None:
    """Return the recovery price for `tier`, or None if the tier or inventory is not eligible."""
    pct = RECOVERY_DISCOUNT_PCT.get(tier) if tier else None  # pragma: no mutate
    if (
        pct is None
        or inventory.open_seats <= 0
        or inventory.cost_floor >= inventory.standard_upgrade_fare
    ):
        return None
    standard = inventory.standard_upgrade_fare
    discounted = standard * (Decimal(100) - pct) / Decimal(100)
    price = (discounted / _STEP).to_integral_value(ROUND_CEILING) * _STEP
    price = min(max(price, inventory.cost_floor), standard)
    return PriceQuote(
        price=price.quantize(_CENT),
        standard_price=standard.quantize(_CENT),
        net_margin=(price - inventory.cost_floor).quantize(_CENT),
    )
