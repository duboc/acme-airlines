import asyncio

from tests.sim.conftest import call


async def test_subscriber_receives_published_cancellation(connect):
    async with connect("occ") as sub, connect("occ") as pub:
        waiter = asyncio.create_task(call(sub, "flight_ops.subscribe", cursor=0, timeout_s=5))
        await asyncio.sleep(0.05)
        await call(pub, "flight_ops.publish_cancellation", flight="AC1234",
                   reason="unscheduled maintenance")  # fmt: skip
        got = await waiter
    event = got["events"][0]
    assert (event["type"], event["flight"]) == ("flight_cancelled", "AC1234")
    assert event["reason"] == "unscheduled maintenance"
    assert got["next_cursor"] == 1


async def test_subscribe_times_out_empty_and_cursor_skips_seen(connect, sim):
    async with connect("occ") as s:
        assert (await call(s, "flight_ops.subscribe", cursor=0, timeout_s=0))["events"] == []
        await call(s, "flight_ops.publish_cancellation", flight="AC1234", reason="x")
        again = await call(s, "flight_ops.subscribe", cursor=1, timeout_s=0)
    assert again["events"] == []
    assert sim.world.flights["AC1234"].status == "cancelled"


async def test_unknown_flight_is_an_error(connect):
    async with connect("occ") as s:
        res = await s.call_tool(
            "flight_ops.publish_cancellation", {"flight": "ZZ999", "reason": "x"}
        )
    assert res.isError
