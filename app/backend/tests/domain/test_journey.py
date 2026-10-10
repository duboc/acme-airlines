from uuid import uuid4

import pytest
from pydantic import ValidationError

from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus as S
from acme.domain.journey import InvalidTransition, JourneyState
from acme.domain.models import Offer

ALL = list(S)
LEGAL = {
    (S.DETECTED, S.RESOLVING),
    (S.RESOLVING, S.RESOLVED),
    (S.RESOLVED, S.REPLANNING),
    (S.REPLANNING, S.RESOLVED),
    (S.RESOLVED, S.CLOSED),
}
PATHS = {
    S.DETECTED: [],
    S.RESOLVING: [S.RESOLVING],
    S.RESOLVED: [S.RESOLVING, S.RESOLVED],
    S.REPLANNING: [S.RESOLVING, S.RESOLVED, S.REPLANNING],
    S.CLOSED: [S.RESOLVING, S.RESOLVED, S.CLOSED],
}


def journey_in(state):
    j = JourneyState("J-0a1b2c3d", FixedClock(DISRUPTION_DETECTED_AT))
    for step in PATHS[state]:
        j.transition(step)
    assert j.status == state
    return j


def offer():
    return Offer(
        offer_id=uuid4(), journey_id="J-0a1b2c3d", flight="AC1252", cabin="premium_business",
        price="135", standard_price="290", net_margin="80", currency="USD",
        origin="sales", optional=True,
    )  # fmt: skip


@pytest.mark.parametrize("src", ALL)
@pytest.mark.parametrize("dst", ALL)
def test_transition_matrix(src, dst):
    j = journey_in(src)
    if (src, dst) in LEGAL:
        j.transition(dst)
        assert j.status == dst
    else:
        with pytest.raises(InvalidTransition):
            j.transition(dst)
        assert j.status == src


def test_detected_straight_to_closed_is_invalid():
    with pytest.raises(InvalidTransition):
        journey_in(S.DETECTED).transition(S.CLOSED)


@pytest.mark.parametrize("status", ["proposed", "approved", "sent"])
def test_leaving_resolved_invalidates_live_offers(status):
    j = journey_in(S.RESOLVED)
    rec = j.add_offer(offer())
    rec.status = status
    j.resolution_acknowledged = True
    j.transition(S.REPLANNING)
    assert rec.status == "invalidated"
    assert j.resolution_acknowledged is False


def test_terminal_offer_statuses_survive_and_closing_invalidates_live():
    j = journey_in(S.RESOLVED)
    done, live = j.add_offer(offer()), j.add_offer(offer())
    done.status = "accepted"
    j.transition(S.CLOSED)
    assert (done.status, live.status) == ("accepted", "invalidated")


def test_ack_reset_on_resolving():
    j = journey_in(S.DETECTED)
    j.resolution_acknowledged = True
    j.transition(S.RESOLVING)
    assert j.resolution_acknowledged is False


def test_timeline_ordered_and_immutable():
    clock = FixedClock(DISRUPTION_DETECTED_AT)
    j = JourneyState("J-0a1b2c3d", clock)
    j.append_event("ops", "detected", {"flight": "AC1234"})
    j.transition(S.RESOLVING, actor="irrops")
    kinds = [e.type for e in j.timeline]
    assert kinds == ["detected", "status_changed"]
    assert j.timeline[0].timestamp == DISRUPTION_DETECTED_AT
    with pytest.raises(ValidationError):
        j.timeline[0].actor = "x"
    assert isinstance(j.timeline, tuple)
    assert not hasattr(j.timeline, "append")
    assert len(j.timeline) == 2
