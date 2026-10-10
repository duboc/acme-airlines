import pytest

from acme.domain.models import Passenger
from acme.policy.margin_opt import RECOVERY_DISCOUNT_PCT
from tests.sim.conftest import call


async def test_member_get_diamond_with_benefits(connect):
    async with connect("loyalty") as s:
        m = await call(s, "member.get", passenger_id="P-MARIANA")
    assert m["tier"] == "diamond" and m["member_id"] == "FFP-0001"
    assert m["status_miles"] == 250000
    assert "priority_rebooking" in m["benefits"] and "lounge_access" in m["benefits"]


async def test_courtesy_eligible_for_mariana_with_rule_reference(connect):
    async with connect("loyalty") as s:
        r = await call(s, "courtesy.eligibility", passenger_id="P-MARIANA", kind="upgrade_recovery")
    assert r["eligible"] is True and r["tier"] == "diamond"
    assert r["rule"] == "acme.policy.margin_opt.RECOVERY_DISCOUNT_PCT"
    assert r["discount_pct"] == "55"


@pytest.mark.parametrize("tier", ["basic", "silver", "gold", "diamond"])
async def test_eligibility_follows_margin_opt_table(connect, sim, tier):
    sim.world.passengers["P-T"] = Passenger(passenger_id="P-T", name="T", tier=tier)
    async with connect("loyalty") as s:
        r = await call(s, "courtesy.eligibility", passenger_id="P-T", kind="upgrade_recovery")
        m = await call(s, "member.get", passenger_id="P-T")
    assert r["eligible"] is (tier in RECOVERY_DISCOUNT_PCT)
    assert m["status_miles"] == 0 and m["tier"] == tier
    if not r["eligible"]:
        assert r["discount_pct"] is None


async def test_unknown_passenger_and_kind_are_errors(connect):
    async with connect("loyalty") as s:
        a = await s.call_tool("member.get", {"passenger_id": "P-NOPE"})
        b = await s.call_tool(
            "courtesy.eligibility", {"passenger_id": "P-NOPE", "kind": "upgrade_recovery"}
        )
        c = await s.call_tool(
            "courtesy.eligibility", {"passenger_id": "P-MARIANA", "kind": "free_flight"}
        )
    assert a.isError and b.isError and c.isError


async def test_registered():
    from acme.sim.app import DEFAULT_REGISTRY

    assert "loyalty" in DEFAULT_REGISTRY
