"""Simulated Loyalty/FFP system: tier, benefits and post-disruption courtesy eligibility.

Eligibility is derived from `margin_opt.RECOVERY_DISCOUNT_PCT`, the single tier table, so the
loyalty system and the pricing policy cannot disagree. Access control (Customer allowed, Sales
denied) is enforced by the allowlist in #27, not here.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from acme.domain.models import Tier
from acme.policy.margin_opt import RECOVERY_DISCOUNT_PCT
from acme.sim.servers import _server
from acme.sim.world import SimWorld

RULE_REF = "acme.policy.margin_opt.RECOVERY_DISCOUNT_PCT"
BENEFITS: dict[Tier, list[str]] = {
    "basic": [],
    "silver": ["priority_boarding"],
    "gold": ["priority_boarding", "lounge_access", "extra_bag"],
    "diamond": [
        "priority_boarding", "lounge_access", "extra_bag", "priority_rebooking", "seat_courtesy",
    ],
}  # fmt: skip


def build_loyalty(sim: SimWorld) -> FastMCP:
    mcp = _server("loyalty")

    @mcp.tool(name="member.get")
    def member_get(passenger_id: str) -> dict[str, Any]:
        """Tier, status miles and benefits for a passenger."""
        p = sim.get_passenger(passenger_id)
        return {
            "passenger_id": p.passenger_id,
            "member_id": p.loyalty.member_id if p.loyalty else None,
            "tier": p.tier,
            "status_miles": p.loyalty.points if p.loyalty else 0,
            "benefits": BENEFITS[p.tier],
        }

    @mcp.tool(name="courtesy.eligibility")
    def courtesy_eligibility(
        passenger_id: str, kind: Literal["upgrade_recovery"]
    ) -> dict[str, Any]:
        """Is a post-disruption recovery courtesy allowed for this passenger's tier?"""
        p = sim.get_passenger(passenger_id)
        pct = RECOVERY_DISCOUNT_PCT.get(p.tier)
        return {
            "passenger_id": p.passenger_id,
            "kind": kind,
            "tier": p.tier,
            "eligible": pct is not None,
            "discount_pct": None if pct is None else str(pct),
            "rule": RULE_REF,
        }

    return mcp
