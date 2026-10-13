"""#231: bounded model calls, deterministic fallback, and no journey lock held during a call."""

import asyncio
import time

import pytest

from acme.llm.bounded import BoundedLlm, LlmTimeout, load_timeout
from acme.llm.fake import FakeLlm
from acme.llm.port import Other
from tests.customer.test_notification import NOTIFIED_AT, PID, at_2209, make, resolved, sent


class SlowLlm(FakeLlm):
    """Hangs (until released) in compose and/or parse, signalling when a call is in flight."""

    def __init__(self, compose=False, parse=False):
        super().__init__()
        self.hang_compose, self.hang_parse = compose, parse
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def compose_passenger_message(self, context):
        if self.hang_compose:
            self.entered.set()
            await self.release.wait()
        return await super().compose_passenger_message(context)

    async def parse_passenger_request(self, text):
        if self.hang_parse:
            self.entered.set()
            await self.release.wait()
        return await super().parse_passenger_request(text)


async def test_compose_times_out_within_the_bound_and_parse_fails_closed():
    llm = BoundedLlm(SlowLlm(compose=True, parse=True), timeout_s=0.05)
    start = time.monotonic()
    with pytest.raises(LlmTimeout):
        await llm.compose_passenger_message({})
    assert await llm.parse_passenger_request("Perfect, thanks!") == Other(text="Perfect, thanks!")
    assert time.monotonic() - start < 2


def test_timeout_is_configurable_with_a_safe_default():
    assert load_timeout({}) == 8.0
    assert load_timeout({"LLM_TIMEOUT_SECONDS": "2.5"}) == 2.5
    assert load_timeout({"LLM_TIMEOUT_SECONDS": "junk"}) == 8.0
    assert load_timeout({"LLM_TIMEOUT_SECONDS": "-1"}) == 8.0


async def test_notification_falls_back_to_the_template_after_a_timeout():
    rt = make(BoundedLlm(SlowLlm(compose=True), timeout_s=0.05))
    rec = await resolved(rt)
    await at_2209(rt)
    (msg,) = sent(rt)
    assert "AC1240" in msg.text
    (ev,) = [e for e in rec.state.timeline if e.type == "notification_sent"]
    assert ev.payload["source"] == "template"


async def test_timed_out_classifier_never_acknowledges_so_no_offer_can_follow():
    rt = make(BoundedLlm(SlowLlm(parse=True), timeout_s=0.05))
    rec = await resolved(rt)
    await at_2209(rt)
    await rt.handle_inbound(PID, "Perfect, thanks!")
    assert not rec.state.resolution_acknowledged
    assert rec.state.open_requests  # handed to a person instead


async def test_no_lock_held_during_a_slow_compose_and_commands_proceed():
    slow = SlowLlm(compose=True)
    rt = make(slow)
    rec = await resolved(rt)
    jid = rec.state.journey_id
    await _advance(rt)
    tick = asyncio.create_task(rt.tick())
    await asyncio.wait_for(slow.entered.wait(), 5)  # compose is now in flight
    assert not rt.journey_lock(jid).locked()
    # a concurrent API command is not blocked by the hung model call
    await asyncio.wait_for(rt.handle_inbound(PID, "Perfect, thanks!"), 5)
    assert not tick.done()
    slow.release.set()
    await asyncio.wait_for(tick, 5)
    assert len(sent(rt)) == 1


async def test_no_lock_held_during_a_slow_classification():
    slow = SlowLlm(parse=True)
    rt = make(slow)
    rec = await resolved(rt)
    inbound = asyncio.create_task(rt.handle_inbound(PID, "Perfect, thanks!"))
    await asyncio.wait_for(slow.entered.wait(), 5)
    assert not rt.journey_lock(rec.state.journey_id).locked()
    slow.release.set()
    await asyncio.wait_for(inbound, 5)
    assert rec.state.resolution_acknowledged


async def test_state_that_became_ineligible_during_the_call_sends_nothing():
    slow = SlowLlm(compose=True)
    rt = make(slow)
    rec = await resolved(rt)
    await _advance(rt)
    tick = asyncio.create_task(rt.tick())
    await asyncio.wait_for(slow.entered.wait(), 5)
    rec.state.append_event("customer_agent", "notification_sent", {"source": "other"})
    slow.release.set()
    await asyncio.wait_for(tick, 5)
    assert sent(rt) == []  # re-validated under the lock: already notified, no duplicate


async def test_facts_that_changed_during_the_call_send_the_template_of_the_new_facts():
    slow = SlowLlm(compose=True)
    rt = make(slow)
    rec = await resolved(rt)
    await _advance(rt)
    tick = asyncio.create_task(rt.tick())
    await asyncio.wait_for(slow.entered.wait(), 5)
    rec.state.append_event("customer_agent", "seat_held", {"seat": "9Z"})
    slow.release.set()
    await asyncio.wait_for(tick, 5)
    (msg,) = sent(rt)
    assert "9Z" in msg.text and "14A" not in msg.text
    (ev,) = [e for e in rec.state.timeline if e.type == "notification_sent"]
    assert ev.payload["source"] == "template"


async def _advance(rt):
    rt.clock.advance(NOTIFIED_AT - rt.clock.now())
