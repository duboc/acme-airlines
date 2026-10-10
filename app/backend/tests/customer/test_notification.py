from datetime import datetime, time, timedelta

import pytest

from acme.customer.change import RecordingChangeAgent
from acme.customer.runtime import CustomerRuntime
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT, GRU
from acme.domain.journey import DisruptionStatus as S
from acme.llm.fake import FakeLlm
from acme.llm.port import DepartureAfter, Other

PID = "P-MARIANA"
NOTIFIED_AT = datetime(2026, 10, 9, 22, 9, tzinfo=GRU)
FACTS = ("AC1234", "AC1240", "08:15", "14A", "HT8842", "transfer", "voucher")


class BadLlm(FakeLlm):
    async def compose_passenger_message(self, context):
        return "Your journey is sorted, see you soon!"  # no resolution facts


class BoomLlm(FakeLlm):
    async def compose_passenger_message(self, context):
        raise RuntimeError("llm down")


def make(llm=None):
    return CustomerRuntime(
        FixedClock(DISRUPTION_DETECTED_AT),
        llm=llm or FakeLlm(),
        change_agent=RecordingChangeAgent(),
    )


async def resolved(rt):
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    assert rt.journeys[jid].state.status == S.RESOLVED
    return rt.journeys[jid]


async def at_2209(rt):
    rt.clock.advance(NOTIFIED_AT - rt.clock.now())
    await rt.tick()


def sent(rt):
    return [m for m in rt.messages.get(PID, []) if m.direction == "outbound"]


async def test_silent_until_notification_is_due_then_exactly_one_message():
    rt = make()
    await resolved(rt)
    rt.clock.advance(timedelta(minutes=2))  # 22:05
    await rt.tick()
    assert rt.messages == {}
    await at_2209(rt)
    await rt.tick()
    (msg,) = sent(rt)
    assert msg.timestamp == NOTIFIED_AT
    for fact in FACTS:
        assert fact in msg.text, fact
    assert "meal_voucher" in msg.attachments and msg.buttons == []


async def test_notification_event_records_source_llm():
    rt = make()
    rec = await resolved(rt)
    await at_2209(rt)
    (ev,) = [e for e in rec.state.timeline if e.type == "notification_sent"]
    assert ev.payload["source"] == "llm" and ev.payload["message_id"]


@pytest.mark.parametrize("llm", [BadLlm(), BoomLlm()])
async def test_template_fallback_when_llm_misses_facts_or_fails(llm):
    rt = make(llm)
    rec = await resolved(rt)
    await at_2209(rt)
    (msg,) = sent(rt)
    for fact in FACTS:
        assert fact in msg.text, fact
    (ev,) = [e for e in rec.state.timeline if e.type == "notification_sent"]
    assert ev.payload["source"] == "template"


async def test_no_notification_while_journey_not_resolved():
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())
    rt.world.flights["AC1240"].status = "cancelled"
    rt.world.flights["AC1252"].status = "cancelled"
    await rt.trigger_cancellation("AC1234", "x")
    await at_2209(rt)
    assert rt.messages == {}


async def test_ack_sets_resolution_acknowledged():
    rt = make()
    rec = await resolved(rt)
    await at_2209(rt)
    await rt.handle_inbound(PID, "Perfect, thanks!")
    assert rec.state.resolution_acknowledged is True
    assert [e.type for e in rec.state.timeline][-1] == "resolution_acknowledged"
    assert rec.state.status == S.RESOLVED


async def test_departure_after_moves_to_replanning_and_hands_off():
    rt = make()
    rec = await resolved(rt)
    await at_2209(rt)
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    assert rec.state.status == S.REPLANNING
    assert rec.state.resolution_acknowledged is False
    (req,) = rec.state.open_requests
    assert req.request_id and "10:00" in req.text
    assert rt.change_agent.requests == [(rec.state.journey_id, req.request_id, time(10, 0))]
    assert [m.direction for m in rt.messages[PID]][-2:] == ["inbound", "outbound"]


async def test_ack_with_open_request_does_not_acknowledge():
    rt = make()
    rec = await resolved(rt)
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    await rt.handle_inbound(PID, "Perfect, thanks!")
    assert rec.state.resolution_acknowledged is False and rec.state.status == S.REPLANNING


async def test_other_is_honest_and_needs_human_keeps_request_open():
    rt = make(FakeLlm({"Can you upgrade me for free?": Other(text="Can you upgrade me for free?")}))
    rec = await resolved(rt)
    await rt.handle_inbound(PID, "Can you upgrade me for free?")
    (req,) = rec.state.open_requests
    assert not req.resolved
    assert any(e.type == "needs_human" for e in rec.state.timeline)
    last = rt.messages[PID][-1]
    assert last.direction == "outbound" and "colleague" in last.text


async def test_scripted_parse_and_unknown_passenger_journey():
    rt = make(FakeLlm({"later pls": DepartureAfter(time=time(11, 0))}))
    rec = await resolved(rt)
    await rt.handle_inbound(PID, "later pls")
    assert rec.state.status == S.REPLANNING and "11:00" in rec.state.open_requests[0].text
    await rt.handle_inbound("P-NOBODY", "hi")  # no journey: recorded, no crash
    assert rt.messages["P-NOBODY"][0].direction == "inbound"


async def test_inbound_before_resolved_is_recorded_only():
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm(), auto_duty_of_care=False)
    (jid,) = await rt.trigger_cancellation("AC1234", "x")
    rec = rt.journeys[jid]
    await rt.handle_inbound(PID, "Perfect, thanks!")
    assert rec.state.resolution_acknowledged is False and rec.state.status == S.RESOLVING
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    assert rec.state.status == S.RESOLVING and len(rec.state.open_requests) == 1
