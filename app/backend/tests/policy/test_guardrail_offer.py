from decimal import Decimal
from uuid import uuid4

import pytest

from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT, mariana_world
from acme.domain.journey import DisruptionStatus as S
from acme.domain.journey import JourneyState, OfferRecord, PassengerRequest
from acme.domain.models import Offer
from acme.policy.guardrail_offer import GuardrailDecision, evaluate

PATHS = {
    S.DETECTED: [],
    S.RESOLVING: [S.RESOLVING],
    S.RESOLVED: [S.RESOLVING, S.RESOLVED],
    S.REPLANNING: [S.RESOLVING, S.RESOLVED, S.REPLANNING],
    S.CLOSED: [S.RESOLVING, S.RESOLVED, S.CLOSED],
}
INVENTORY = mariana_world().inventory[0]


def make_offer(**over):
    base = dict(
        offer_id=uuid4(), journey_id="J-0a1b2c3d", flight="AC1252", cabin="premium_business",
        price="135", standard_price="290", net_margin="80", currency="USD",
        origin="sales", optional=True,
    )  # fmt: skip
    return Offer(**{**base, **over})


def make_journey(status=S.RESOLVED, ack=True, tier="diamond", flight="AC1252"):
    j = JourneyState(
        "J-0a1b2c3d", FixedClock(DISRUPTION_DETECTED_AT), passenger_tier=tier, current_flight=flight
    )
    for step in PATHS[status]:
        j.transition(step)
    j.resolution_acknowledged = ack
    return j


def add_other(j, status, **offer_over):
    rec = OfferRecord(offer=make_offer(**offer_over), status=status)
    j.offers.append(rec)
    return rec


def run(j=None, offer=None, inv=INVENTORY):
    return evaluate(j or make_journey(), offer or make_offer(), inv)


def test_happy_path_approved_and_lists_satisfied_rules():
    d = run()
    assert isinstance(d, GuardrailDecision) and d.approved
    assert d.reasons == [
        "disruption_resolved",
        "resolution_acknowledged",
        "genuine_discount",
        "margin_verified",
        "zero_pressure",
        "origin_and_flight_verified",
    ]


@pytest.mark.parametrize("status", [S.DETECTED, S.RESOLVING, S.REPLANNING, S.CLOSED])
def test_rule1_not_resolved(status):
    d = run(make_journey(status))
    assert not d.approved and "disruption_not_resolved" in d.reasons


def test_rule2_not_acknowledged():
    d = run(make_journey(ack=False))
    assert not d.approved and d.reasons == ["resolution_not_acknowledged"]


def test_rule2_open_request_pending_ignores_resolved_requests():
    j = make_journey()
    j.open_requests.append(PassengerRequest(request_id="R1", text="seat?"))
    d = run(j)
    assert not d.approved and d.reasons == ["open_request_pending"]


def test_rule2_requests_list_empty_means_ok():
    assert run(make_journey()).approved


@pytest.mark.parametrize(
    "offer_over, inv_over, tier, expected",
    [
        ({"standard_price": "990"}, {}, "diamond", {"price_claim_mismatch"}),
        ({}, {"open_seats": 0}, "diamond", {"no_inventory"}),
        ({"price": "290", "net_margin": "235"}, {}, "diamond", {"not_a_genuine_discount"}),
        ({"price": "300", "net_margin": "245"}, {}, "diamond", {"not_a_genuine_discount"}),
        # below standard but above the tier's recovery price
        ({"price": "200", "net_margin": "145"}, {}, "diamond", {"not_a_genuine_discount"}),
        ({}, {}, "silver", {"tier_not_eligible"}),
        ({}, {}, "basic", {"tier_not_eligible"}),
        ({}, {}, None, {"tier_not_eligible"}),
        # inventory describes a different flight / cabin than the offer
        ({"flight": "AC1240"}, {}, "diamond", {"no_inventory", "flight_mismatch"}),
    ],
)
def test_rule3_each_condition_alone(offer_over, inv_over, tier, expected):
    inv = INVENTORY.model_copy(update=inv_over)
    j = make_journey(tier=tier)
    d = evaluate(j, make_offer(**offer_over), inv)
    assert not d.approved
    assert set(d.reasons) == expected


def test_rule3_gold_cap():
    j = make_journey(tier="gold")
    ok = evaluate(j, make_offer(price="175", net_margin="120"), INVENTORY)
    assert ok.approved
    bad = evaluate(j, make_offer(price="176", net_margin="121"), INVENTORY)
    assert bad.reasons == ["not_a_genuine_discount"]


def test_rule3_inventory_cabin_mismatch():
    inv = INVENTORY.model_copy(update={"cabin": "economy"})
    assert "no_inventory" in run(inv=inv).reasons


def test_rule4_negative_margin_and_claim_mismatch():
    d = run(offer=make_offer(price="40", net_margin="80"))
    assert not d.approved
    assert {"negative_margin", "price_claim_mismatch"} <= set(d.reasons)


def test_rule4_claim_mismatch_only():
    d = run(offer=make_offer(net_margin="81"))
    assert d.reasons == ["price_claim_mismatch"]


def test_rule4_zero_margin_ok():
    inv = INVENTORY.model_copy(update={"cost_floor": Decimal("135")})
    assert evaluate(make_journey(), make_offer(net_margin="0"), inv).approved


def test_rule4_margin_minus_one_rejected():
    inv = INVENTORY.model_copy(update={"cost_floor": Decimal("55")})
    d = evaluate(make_journey(), make_offer(price="54", net_margin="-1"), inv)
    assert not d.approved
    assert "negative_margin" in d.reasons
    assert "price_claim_mismatch" not in d.reasons


def test_rule5_not_optional_cannot_be_constructed_but_is_checked():
    offer = make_offer().model_copy(update={"optional": False})
    assert run(offer=offer).reasons == ["not_optional"]


@pytest.mark.parametrize("status", ["approved", "sent", "accepted", "declined"])
def test_rule5_other_answered_or_live_offer_blocks(status):
    j = make_journey()
    add_other(j, status, flight="AC1240")
    d = evaluate(j, make_offer(), INVENTORY)
    assert d.reasons == ["offer_already_made"]


@pytest.mark.parametrize("status", ["proposed", "rejected", "invalidated"])
def test_rule5_other_statuses_on_other_flight_do_not_block(status):
    j = make_journey()
    add_other(j, status, flight="AC1240")
    assert evaluate(j, make_offer(), INVENTORY).approved


def test_rule5_invalidated_same_flight_blocks_reproposal():
    j = make_journey()
    add_other(j, "invalidated", flight="AC1252")
    assert evaluate(j, make_offer(), INVENTORY).reasons == ["offer_already_made"]


def test_rule5_invalidated_on_old_flight_allows_new_flight():
    j = make_journey(flight="AC1252")
    add_other(j, "invalidated", flight="AC1240")
    assert evaluate(j, make_offer(), INVENTORY).approved


@pytest.mark.parametrize("status", ["proposed", "approved", "sent"])
def test_rule5_offer_itself_is_excluded_on_recheck_while_it_is_live(status):
    j = make_journey()
    offer = make_offer()
    j.offers.append(OfferRecord(offer=offer, status=status))
    assert evaluate(j, offer, INVENTORY).approved


def test_rule5_skipping_itself_does_not_hide_another_offer_listed_after_it():
    j = make_journey()
    offer = make_offer()
    j.offers.append(OfferRecord(offer=offer, status="sent"))  # itself, first in the list
    add_other(j, "declined", flight="AC1252")  # another one, after it
    assert evaluate(j, offer, INVENTORY).reasons == ["offer_already_made"]


@pytest.mark.parametrize("status", ["invalidated", "declined", "accepted"])
def test_rule5_an_offer_already_settled_or_invalidated_is_never_revived(status):
    """#340: a stale Accept on an invalidated offer, after the passenger re-acknowledged the
    resolution on the same flight, charged $135: the offer itself was always skipped."""
    j = make_journey()
    offer = make_offer()
    j.offers.append(OfferRecord(offer=offer, status=status))
    decision = evaluate(j, offer, INVENTORY)
    assert not decision.approved and decision.reasons == ["offer_already_made"]


def test_rule6_wrong_origin():
    offer = make_offer().model_copy(update={"origin": "customer"})
    assert run(offer=offer).reasons == ["wrong_origin"]


def test_rule6_flight_mismatch():
    j = make_journey(flight="AC1240")
    assert evaluate(j, make_offer(), INVENTORY).reasons == ["flight_mismatch"]


def test_rule6_journey_without_current_flight_fails_closed():
    j = make_journey(flight=None)
    assert "flight_mismatch" in evaluate(j, make_offer(), INVENTORY).reasons


def test_all_failed_rules_listed_without_duplicates():
    j = make_journey(status=S.DETECTED, ack=False, tier="basic", flight="AC1240")
    j.open_requests.append(PassengerRequest(request_id="R1", text="x"))
    offer = make_offer(standard_price="990", price="40", net_margin="80").model_copy(
        update={"origin": "customer", "optional": False}
    )
    d = evaluate(j, offer, INVENTORY.model_copy(update={"open_seats": 0}))
    assert not d.approved
    assert len(d.reasons) == len(set(d.reasons))
    assert set(d.reasons) == {
        "disruption_not_resolved", "resolution_not_acknowledged", "open_request_pending",
        "price_claim_mismatch", "no_inventory", "tier_not_eligible", "negative_margin",
        "not_optional", "wrong_origin", "flight_mismatch",
    }  # fmt: skip


def test_ac1_rule6_another_journeys_offer_is_never_approved():
    decision = evaluate(make_journey(), make_offer(journey_id="J-deadbeef"), INVENTORY)
    assert not decision.approved and decision.reasons == ["journey_mismatch"]


def test_rule5_declined_offer_on_subsequently_cancelled_flight_allows_new_disruption_offer():
    j = make_journey(flight="AC1252")
    add_other(j, "declined", flight="AC1240")
    j.append_event("occ", "cancellation_detected", {"flight": "AC1240", "reason": "weather"})
    assert evaluate(j, make_offer(flight="AC1252"), INVENTORY).approved

    # A second offer in the new disruption after a decline on AC1252 is still blocked.
    add_other(j, "declined", flight="AC1252")
    assert evaluate(j, make_offer(flight="AC1252"), INVENTORY).reasons == ["offer_already_made"]


async def test_decline_followed_by_secondary_cancellation_allows_one_new_pitch():
    from datetime import timedelta

    import httpx

    from acme.api.app import create_app
    from acme.customer.conversion import decline_offer
    from acme.domain.models import CabinInventory
    from acme.sales.client import A2ACustomerClient
    from acme.sales.radar import SalesRadar
    from acme.sim.client import SimClient, inprocess_connector

    app = create_app(FixedClock(DISRUPTION_DETECTED_AT))
    rt = app.state.runtime
    # Seed premium inventory on AC1240 as well so Disruption 1 can pitch and decline on AC1240.
    rt.world.inventory.append(
        CabinInventory(
            flight_number="AC1240",
            cabin="premium_business",
            open_seats=4,
            standard_upgrade_fare=Decimal("290.00"),
            cost_floor=Decimal("55.00"),
        )
    )
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://customer")
    radar = SalesRadar(A2ACustomerClient(http), SimClient(inprocess_connector(rt.sim, "sales")))
    try:
        (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
        rec = rt.journeys[jid]
        rt.clock.advance(timedelta(minutes=7))
        await rt.tick()
        await rt.handle_inbound("P-MARIANA", "Perfect, thanks!")
        assert len(await radar.tick()) == 1
        assert await decline_offer(rt, rec) == "declined"
        assert rec.state.status == S.CLOSED

        # Secondary disruption: AC1240 is cancelled -> rebooked to AC1252, notified, acknowledged.
        assert await rt.trigger_cancellation("AC1240", "weather") == [jid]
        assert rec.state.status == S.RESOLVED
        rt.clock.advance(timedelta(minutes=7))
        await rt.tick()
        await rt.handle_inbound("P-MARIANA", "Perfect, thanks!")
        assert rec.state.resolution_acknowledged is True

        # SalesRadar and guardrail_offer permit 1 pitch on AC1252 for the new disruption.
        sent = await radar.tick()
        assert len(sent) == 1 and sent[0].flight == "AC1252"
        assert [o.status for o in rec.state.offers] == ["sent"]

        # Declining the second disruption's offer still blocks any further offer in Disruption 2.
        await decline_offer(rt, rec)
        assert await radar.tick() == []
    finally:
        await http.aclose()
