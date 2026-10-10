"""Layer 3b (policy): property tests. Approval implies all six guardrail rules.

The oracle below is written from the brief / ADR 0100 independently of `guardrail_offer`.
"""

from decimal import Decimal
from uuid import uuid4

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from acme.domain.journey import DisruptionStatus as S
from acme.domain.journey import OfferRecord, PassengerRequest
from acme.domain.models import CabinInventory
from acme.policy.guardrail_offer import evaluate
from tests.governance.conftest import INVENTORY, make_journey, make_offer

SETTINGS = settings(
    max_examples=400, derandomize=True, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)


def mostly(good, *bad, weight: int = 6):
    """Biased choice: the valid value most of the time, so approvals are actually reached."""
    return st.sampled_from([good] * weight + list(bad))


STATUSES = mostly(S.RESOLVED, S.DETECTED, S.RESOLVING, S.REPLANNING, S.CLOSED)
TIERS = mostly("diamond", "basic", "silver", "gold", None)
OFFER_STATUSES = st.sampled_from(
    ["proposed", "approved", "rejected", "sent", "invalidated", "accepted", "declined"]
)
FLIGHTS = mostly("AC1252", "AC1240", "AC1260")
BLOCKING = {"approved", "sent", "accepted", "declined"}
DISCOUNT_CAP = {"diamond": Decimal("135"), "gold": Decimal("175")}  # recovery price, fixture fare


@st.composite
def scenarios(draw):
    journey = make_journey(
        draw(STATUSES), draw(mostly(True, False)), tier=draw(TIERS), flight=draw(FLIGHTS)
    )
    for i in range(draw(mostly(0, 1, 2, weight=8))):
        journey.open_requests.append(PassengerRequest(request_id=f"r{i}", text="x"))
    price = draw(mostly(Decimal(135), *map(Decimal, (0, 10, 55, 100, 140, 200, 289, 290, 400))))
    margin = draw(mostly(price - INVENTORY.cost_floor, Decimal(80), Decimal(500), Decimal(-1)))
    offer = make_offer(
        price=price,
        standard_price=draw(mostly(Decimal(290), Decimal(900), Decimal(135))),
        net_margin=margin,
        flight=draw(FLIGHTS),
    )
    # origin/optional are Literal-typed; bypass validation to attack the policy layer itself.
    offer = offer.model_construct(**{**offer.__dict__, "origin": draw(mostly("sales", "customer"))})
    for status, same_id, same_flight in draw(
        st.one_of(
            st.just([]),
            st.lists(st.tuples(OFFER_STATUSES, st.booleans(), st.booleans()), max_size=3),
        )
    ):
        other = make_offer(
            offer_id=offer.offer_id if same_id else uuid4(),
            flight=offer.flight if same_flight else "AC1240",
        )
        journey.offers.append(OfferRecord(offer=other, status=status))
    inventory = INVENTORY.model_copy(
        update={
            "open_seats": draw(mostly(4, 0, 1)),
            "flight_number": draw(FLIGHTS),
            "standard_upgrade_fare": draw(mostly(Decimal(290), Decimal(300))),
        }
    )
    return journey, offer, inventory


def six_rules(journey, offer, inv: CabinInventory) -> list[bool]:
    cap = DISCOUNT_CAP.get(journey.passenger_tier or "")
    others = [r for r in journey.offers if r.offer.offer_id != offer.offer_id]
    return [
        journey.status is S.RESOLVED,
        journey.resolution_acknowledged and not journey.open_requests,
        offer.standard_price == inv.standard_upgrade_fare
        and inv.flight_number == offer.flight
        and inv.cabin == offer.cabin
        and inv.open_seats > 0
        and offer.price < inv.standard_upgrade_fare
        and cap is not None
        and offer.price <= cap * inv.standard_upgrade_fare / Decimal(290),
        offer.price - inv.cost_floor >= 0 and offer.price - inv.cost_floor == offer.net_margin,
        offer.optional is True
        and not any(
            r.status in BLOCKING or (r.status == "invalidated" and r.offer.flight == offer.flight)
            for r in others
        ),
        offer.origin == "sales" and journey.current_flight == offer.flight,
    ]


def test_approval_implies_all_six_rules_hold():
    approvals = 0

    @given(scenarios())
    @SETTINGS
    def check(case):
        nonlocal approvals
        journey, offer, inv = case
        if evaluate(journey, offer, inv).approved:
            approvals += 1
            assert all(six_rules(journey, offer, inv)), six_rules(journey, offer, inv)

    check()
    assert approvals >= 20, f"only {approvals} approvals generated: the property is near-vacuous"


@given(scenarios())
@SETTINGS
def test_every_decision_is_explained_and_never_approves_with_a_failed_rule(case):
    journey, offer, inv = case
    d = evaluate(journey, offer, inv)
    assert d.reasons, "a decision always carries reasons"
    if not all(six_rules(journey, offer, inv)):
        assert not d.approved


@given(scenarios())
@SETTINGS
def test_a_live_re_evaluation_of_the_same_offer_is_idempotent(case):
    journey, offer, inv = case
    first = evaluate(journey, offer, inv)
    journey.offers.append(OfferRecord(offer=offer, status="sent"))  # its own record: excluded
    again = evaluate(journey, offer, inv)
    assert (first.approved, first.reasons) == (again.approved, again.reasons)


def test_the_property_strategy_can_reach_an_approval():
    """Guards the properties against vacuity: the oracle's all-pass region is reachable."""
    journey, offer, inv = make_journey(), make_offer(), INVENTORY
    assert all(six_rules(journey, offer, inv)) and evaluate(journey, offer, inv).approved


# --- each rule on its own, hand-written -------------------------------------------------------
def _rejects(reason, journey=None, offer=None, inv=INVENTORY):
    d = evaluate(journey or make_journey(), offer or make_offer(), inv)
    assert not d.approved and reason in d.reasons


def test_rule1_not_resolved():
    _rejects("disruption_not_resolved", make_journey(S.RESOLVING))


def test_rule2_unacknowledged_and_open_request():
    _rejects("resolution_not_acknowledged", make_journey(ack=False))
    j = make_journey()
    j.open_requests.append(PassengerRequest(request_id="r1", text="after 10"))
    _rejects("open_request_pending", j)


def test_rule3_ineligible_tier_and_unknown_tier():
    _rejects("tier_not_eligible", make_journey(tier="silver"))
    _rejects("tier_not_eligible", make_journey(tier=None))


def test_rule4_margin_must_be_recomputed():
    _rejects("price_claim_mismatch", offer=make_offer(net_margin=Decimal("81")))


def test_rule5_another_live_offer_blocks_even_a_perfect_one():
    for status in ("approved", "sent", "accepted", "declined"):
        j = make_journey()
        j.offers.append(OfferRecord(offer=make_offer(), status=status))
        _rejects("offer_already_made", j)


def test_rule5_invalidated_offer_on_the_same_flight_blocks_but_other_flight_does_not():
    j = make_journey()
    j.offers.append(OfferRecord(offer=make_offer(), status="invalidated"))
    _rejects("offer_already_made", j)
    j2 = make_journey()
    j2.offers.append(OfferRecord(offer=make_offer(flight="AC1240"), status="invalidated"))
    assert evaluate(j2, make_offer(), INVENTORY).approved


def test_rule6_wrong_origin_and_wrong_flight():
    forged = make_offer().model_construct(**{**make_offer().__dict__, "origin": "customer"})
    _rejects("wrong_origin", offer=forged)
    _rejects("flight_mismatch", make_journey(flight="AC1240"))
    _rejects("flight_mismatch", make_journey(flight=None))
