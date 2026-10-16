"""#488: the retry of a closure that failed after the passenger decided (#445), pinned branch by
branch and at its time boundary so no mutant of `decided_closure_due` survives."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from acme.customer.service_closure import decided_closure_due
from acme.domain.journey import DisruptionStatus as S

T0 = datetime(2026, 10, 9, 22, 30, tzinfo=UTC)
WINDOW = timedelta(minutes=10)


def event(type_, at=T0, **payload):
    return NS(type=type_, timestamp=at, payload=payload)


def due(
    *,
    status=S.RESOLVED,
    open_requests=(),
    offers=("accepted",),
    timeline=None,
    now=T0 + WINDOW,
):
    timeline = [event("verification_failed")] if timeline is None else timeline
    state = NS(
        status=status,
        open_requests=list(open_requests),
        offers=[NS(status=s) for s in offers],
        timeline=timeline,
    )
    rt = NS(clock=NS(now=lambda: now), offer_window=WINDOW)
    return decided_closure_due(rt, NS(state=state))


def test_a_failed_closure_of_a_decided_offer_is_due_exactly_at_the_end_of_the_window():
    assert due(now=T0 + WINDOW) is True  # the boundary itself counts
    assert due(now=T0 + WINDOW + timedelta(seconds=1)) is True
    assert due(now=T0 + WINDOW - timedelta(seconds=1)) is False
    assert due(now=T0) is False


@pytest.mark.parametrize("decided", ["accepted", "declined"])
def test_either_decision_makes_the_retry_due(decided):
    assert due(offers=[decided]) is True


@pytest.mark.parametrize("status", [s for s in S if s is not S.RESOLVED])
def test_only_a_resolved_journey_is_retried(status):
    assert due(status=status) is False


def test_an_open_request_blocks_the_retry():
    assert due(open_requests=["a request"]) is False


@pytest.mark.parametrize("live", ["proposed", "approved", "sent"])
@pytest.mark.parametrize("decided", ["accepted", "declined"])
def test_a_live_offer_blocks_the_retry_whatever_the_order(live, decided):
    assert due(offers=[decided, live]) is False
    assert due(offers=[live, decided]) is False


@pytest.mark.parametrize("other", ["invalidated", "rejected"])
def test_an_offer_that_never_reached_a_decision_does_not_block_a_decided_one(other):
    assert due(offers=[other, "accepted"]) is True
    assert due(offers=["declined", other]) is True


@pytest.mark.parametrize(
    "offers", [[], ["invalidated"], ["rejected"], ["invalidated", "rejected"], ["proposed"]]
)
def test_without_a_decided_offer_there_is_nothing_to_retry(offers):
    assert due(offers=offers) is False


def test_a_decided_offer_with_no_recorded_failure_is_not_retried():
    assert due(timeline=[]) is False
    assert due(timeline=[event("notification_sent"), event("offer_sent")]) is False


def test_a_closure_specialist_escalation_counts_as_a_failure():
    assert due(timeline=[event("needs_human", specialist="closure")]) is True


@pytest.mark.parametrize("payload", [{"specialist": "seat"}, {"specialist": "hotel"}, {}])
def test_an_escalation_of_another_specialist_is_not_a_closure_failure(payload):
    assert due(timeline=[event("needs_human", **payload)]) is False


def test_a_step_style_escalation_is_not_a_closure_failure():
    assert due(timeline=[event("needs_human", step="seat_release")]) is False


def test_the_window_runs_from_the_latest_failure_whichever_comes_first_in_the_list():
    early, late = T0, T0 + timedelta(minutes=8)
    now = early + WINDOW  # the first failure's window is over, the second's is not
    assert (
        due(
            timeline=[event("verification_failed", early), event("verification_failed", late)],
            now=now,
        )
        is False
    )
    assert (
        due(
            timeline=[event("verification_failed", late), event("verification_failed", early)],
            now=now,
        )
        is False
    )
    assert (
        due(
            timeline=[event("verification_failed", early), event("verification_failed", late)],
            now=late + WINDOW,
        )
        is True
    )


def test_the_two_failure_kinds_share_one_clock():
    mixed = [
        event("verification_failed", T0),
        event("needs_human", T0 + timedelta(minutes=3), specialist="closure"),
    ]
    assert due(timeline=mixed, now=T0 + WINDOW) is False
    assert due(timeline=mixed, now=T0 + timedelta(minutes=3) + WINDOW) is True


@pytest.mark.parametrize("closed", [True, False])
async def test_the_retry_forwards_the_runtime_and_the_record_and_returns_the_closure_result(
    monkeypatch, closed
):
    from acme.customer import service_closure

    seen = []

    async def close_loop(rt, rec):
        seen.append((rt, rec))
        return closed

    monkeypatch.setattr(service_closure, "close_loop", close_loop)
    rt, rec = NS(name="rt"), NS(name="rec")
    assert await service_closure.retry_decided_closure(rt, rec) is closed
    assert seen == [(rt, rec)]
