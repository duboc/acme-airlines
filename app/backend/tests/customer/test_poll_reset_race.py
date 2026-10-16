"""A poll that was awaiting when the scenario was reset never writes onto the new world (#466)."""

import asyncio

from acme.customer.runtime import CustomerRuntime
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT


async def test_a_poll_in_flight_across_a_reset_does_not_skip_the_new_worlds_first_event():
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), auto_duty_of_care=False)
    await rt.client.call(
        "occ", "flight_ops.publish_cancellation", flight="AC1234", reason="old world"
    )  # event 0 of the OLD world: a poll with cursor 0 gets it and next_cursor 1
    reached, release, real = asyncio.Event(), asyncio.Event(), rt.client.call

    async def hold_the_subscribe_reply(server, tool, **kwargs):
        reply = await real(server, tool, **kwargs)
        if tool == "flight_ops.subscribe":
            reached.set()
            await release.wait()  # the poll is now "in flight" with the old world's answer
        return reply

    rt.client.call = hold_the_subscribe_reply
    stale_poll = asyncio.create_task(rt.poll())
    await reached.wait()

    rt.reset()  # the new scenario starts: a new world, cursor 0
    await rt.client.call(
        "occ", "flight_ops.publish_cancellation", flight="AC1234", reason="new world"
    )  # event 0 of the NEW world
    release.set()
    assert await stale_poll == []  # it aborted: it opened nothing on the new world
    assert rt._cursor == 0 and rt.journeys == {}  # noqa: SLF001

    (jid,) = await rt.poll()  # the new world's event 0 is still there to be processed
    assert rt.journeys[jid].state.timeline[0].payload["reason"] == "new world"


async def test_a_reset_during_a_poll_without_news_leaves_the_cursor_alone():
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), auto_duty_of_care=False)
    assert await rt.poll() == [] and rt._cursor == 0  # noqa: SLF001
    (jid,) = await rt.trigger_cancellation("AC1234", "x")
    assert rt._cursor == 1 and jid in rt.journeys  # noqa: SLF001 - the normal path is unchanged
