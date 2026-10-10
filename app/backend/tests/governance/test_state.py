"""Layer 4 (state): nothing is proposed or radar-emitted unless the disruption is resolved."""

import pytest

from acme.domain.journey import DisruptionStatus as S
from acme.policy.guardrail_offer import evaluate
from tests.governance.conftest import INVENTORY, make_journey, make_offer
from tests.governance.test_structured_offers import offer_payload

NOT_RESOLVED = [S.DETECTED, S.RESOLVING, S.REPLANNING, S.CLOSED]


@pytest.mark.parametrize("status", NOT_RESOLVED)
def test_the_guardrail_rejects_a_perfect_offer_in_every_non_resolved_status(status):
    d = evaluate(make_journey(status), make_offer(), INVENTORY)
    assert not d.approved and "disruption_not_resolved" in d.reasons


@pytest.mark.parametrize("status", NOT_RESOLVED)
async def test_the_radar_emits_nothing_in_every_non_resolved_status(stack, status):
    state = stack.rt.journeys[stack.jid].state
    state.resolution_acknowledged = True  # even when acknowledged
    _force(state, status)
    assert await stack.radar.tick() == []
    assert state.offers == []


async def test_the_radar_emits_nothing_while_the_resolution_is_unacknowledged(stack):
    state = stack.rt.journeys[stack.jid].state
    state.transition(S.RESOLVED)
    state.resolution_acknowledged = False
    assert await stack.radar.tick() == []
    assert state.offers == []


async def test_the_radar_emits_nothing_after_a_replan_reopens_the_journey(stack):
    state = stack.rt.journeys[stack.jid].state
    state.transition(S.RESOLVED)
    state.resolution_acknowledged = True
    assert len(await stack.radar.tick()) == 1  # allowed once resolved and acknowledged
    state.transition(S.REPLANNING)
    assert await stack.radar.tick() == []
    assert [r.status for r in state.offers] == ["invalidated"]


@pytest.mark.xfail(strict=True, reason="bug #83: propose_offer records proposals in any status")
@pytest.mark.parametrize("status", [S.RESOLVING, S.REPLANNING])
async def test_a_proposal_in_a_non_resolved_status_is_rejected(stack, status):
    state = stack.rt.journeys[stack.jid].state
    _force(state, status)
    from acme.customer.skills import SkillError

    with pytest.raises(SkillError):
        stack.skills.propose_offer(offer_payload(journey_id=stack.jid))
    assert state.offers == []


def _force(state, status):
    if status is S.REPLANNING:
        state.transition(S.RESOLVED)
        state.transition(S.REPLANNING)
    elif status is S.CLOSED:
        state.transition(S.RESOLVED)
        state.transition(S.CLOSED)
    elif status is S.DETECTED:
        state.status = S.DETECTED  # unreachable via transitions: forced to attack the radar
