"""#281: the no-offer closure decision (#229) pinned at its boundaries and its inputs."""

from datetime import timedelta

from acme.customer.service_closure import service_closure_due
from acme.domain.journey import DisruptionStatus as S
from tests.governance.flow import Flow

PID = "P-MARIANA"


async def acknowledged():
    """RESOLVED and acknowledged, no offer ever made; the clock sits at the acknowledgement."""
    f = Flow()
    await f.cancel()
    await f.notify()
    await f.rt.handle_inbound(PID, "Perfect, thanks!")
    rec = f.rt.journeys[f.state.journey_id]
    return f, rec


def acknowledged_at(rec):
    return max(e.timestamp for e in rec.state.timeline if e.type == "resolution_acknowledged")


def move_clock_to(f, when):
    f.rt.clock.advance(when - f.rt.clock.now())


async def test_given_the_window_when_it_ends_exactly_then_the_journey_is_due_and_not_before():
    f, rec = await acknowledged()
    due_at = acknowledged_at(rec) + f.rt.offer_window
    move_clock_to(f, due_at - timedelta(seconds=1))
    assert not service_closure_due(f.rt, rec)
    move_clock_to(f, due_at)
    assert service_closure_due(f.rt, rec)  # the boundary itself counts
    await f.http.aclose()


async def test_given_a_journey_not_resolved_when_acknowledged_then_it_is_never_due():
    f, rec = await acknowledged()
    move_clock_to(f, acknowledged_at(rec) + f.rt.offer_window + timedelta(minutes=5))
    assert service_closure_due(f.rt, rec)  # control: due as it stands
    rec.state.status = next(s for s in S if s is not S.RESOLVED)
    assert rec.state.resolution_acknowledged
    assert not service_closure_due(f.rt, rec)
    await f.http.aclose()


async def test_given_a_resolved_journey_not_acknowledged_then_it_is_never_due():
    f, rec = await acknowledged()
    move_clock_to(f, acknowledged_at(rec) + f.rt.offer_window + timedelta(minutes=5))
    rec.state.resolution_acknowledged = False
    assert rec.state.status is S.RESOLVED
    assert not service_closure_due(f.rt, rec)
    await f.http.aclose()


async def test_given_other_events_after_the_acknowledgement_then_only_the_anchors_set_the_window():
    f, rec = await acknowledged()
    anchor = acknowledged_at(rec)
    move_clock_to(f, anchor + f.rt.offer_window - timedelta(minutes=1))
    rec.state.append_event("customer_agent", "something_else", {})  # not an anchor
    move_clock_to(f, anchor + f.rt.offer_window)
    assert service_closure_due(f.rt, rec)  # counted from the acknowledgement, not the last event
    await f.http.aclose()


async def test_given_a_failed_attempt_when_the_next_window_passes_then_the_retry_waits_for_it():
    f, rec = await acknowledged()
    move_clock_to(f, acknowledged_at(rec) + f.rt.offer_window)
    rec.state.append_event("customer_agent", "service_closure", {"reason": "offer_window_elapsed"})
    assert not service_closure_due(f.rt, rec)  # a further window starts at the attempt
    move_clock_to(f, f.rt.clock.now() + f.rt.offer_window)
    assert service_closure_due(f.rt, rec)
    await f.http.aclose()


async def test_given_the_closure_when_recorded_then_the_customer_agent_is_the_actor():
    from acme.customer.service_closure import close_without_offer

    f, rec = await acknowledged()
    async with f.rt.journey_lock(f.state.journey_id):
        await close_without_offer(f.rt, rec)
    (event,) = f.events("service_closure")
    assert event.actor == "customer_agent" and event.payload == {"reason": "offer_window_elapsed"}
    await f.http.aclose()
