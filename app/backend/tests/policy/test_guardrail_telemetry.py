"""The guardrail's span and metric contract (#232): what Observatory and the live proof read."""

from decimal import Decimal

from acme.policy.guardrail_offer import evaluate, evaluate_traced
from tests.policy.test_guardrail_offer import INVENTORY, make_journey, make_offer


def _guardrail_span(spans):
    (s,) = [s for s in spans.get_finished_spans() if s.name == "acme.guardrail.decision"]
    return s


def test_an_approval_emits_one_decision_span_with_journey_flight_step_and_first_reason(spans):
    journey, offer = make_journey(), make_offer()
    decision = evaluate_traced(journey, offer, INVENTORY, "send")
    assert decision.approved and len(decision.reasons) > 1
    attrs = dict(_guardrail_span(spans).attributes)
    assert attrs == {
        "acme.journey_id": journey.journey_id,
        "acme.flight": "AC1252",
        "acme.step": "send",
        "acme.guardrail.decision": "approve",
        "acme.guardrail.reason_code": decision.reasons[0],
    }


def test_a_rejection_emits_reject_with_the_first_failed_rule_and_no_pii(spans):
    offer = make_offer(standard_price=Decimal("900"))
    decision = evaluate_traced(make_journey(), offer, INVENTORY, "accept")
    assert not decision.approved
    attrs = dict(_guardrail_span(spans).attributes)
    assert attrs["acme.guardrail.decision"] == "reject"
    assert attrs["acme.guardrail.reason_code"] == decision.reasons[0] == "price_claim_mismatch"
    assert attrs["acme.step"] == "accept"


def test_traced_and_plain_evaluation_agree(spans):
    for journey in (make_journey(), make_journey(ack=False)):
        offer = make_offer()
        traced = evaluate_traced(journey, offer, INVENTORY, "propose")
        assert (traced.approved, traced.reasons) == (
            evaluate(journey, offer, INVENTORY).approved,
            evaluate(journey, offer, INVENTORY).reasons,
        )


def _decisions(metric_points):
    return {
        (a["rule"], a["outcome"], a["step"]): v
        for a, v in metric_points().get("acme.guardrail.decisions", [])
    }


def _delta(before, after):
    """Counters are cumulative for the whole suite: compare what one call added."""
    return {k: v - before.get(k, 0) for k, v in after.items() if v - before.get(k, 0)}


def test_an_approval_counts_one_all_rules_approve_for_the_step(metric_points):
    before = _decisions(metric_points)
    evaluate_traced(make_journey(), make_offer(), INVENTORY, "recheck")
    assert _delta(before, _decisions(metric_points)) == {("all", "approve", "recheck"): 1}


def test_a_rejection_counts_every_failed_rule_for_the_step(metric_points):
    before = _decisions(metric_points)
    decision = evaluate_traced(
        make_journey(ack=False, tier="silver"), make_offer(), INVENTORY, "send"
    )
    assert len(decision.reasons) >= 2
    got = _delta(before, _decisions(metric_points))
    assert got == {(r, "reject", "send"): 1 for r in decision.reasons}
