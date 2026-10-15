"""#280: `reuse_sessions()` keeps one in-process sim session per (connector, server) for a block,
closes all of them when it ends (also on error), reopens after a failed call, and shares nothing."""

import gc
from contextlib import asynccontextmanager

import pytest

from acme.sim import client
from acme.sim.client import SimClient, inprocess_connector, reuse_sessions
from acme.sim.world import SimWorld


class Spy:
    """Counts sessions opened/closed through `client._session`; can fail one `call_tool`."""

    def __init__(self, monkeypatch, fail_first_call=False):
        self.opened = self.closed = 0
        self.fail = fail_first_call
        real = client._session
        spy = self

        @asynccontextmanager
        async def session(http, server):
            spy.opened += 1
            try:
                async with real(http, server) as s:
                    yield spy.wrap(s)
            finally:
                spy.closed += 1

        monkeypatch.setattr(client, "_session", session)

    def wrap(self, s):
        spy = self

        class Wrapped:
            async def call_tool(self, *a, **k):
                if spy.fail:
                    spy.fail = False
                    raise RuntimeError("transport broke")
                return await s.call_tool(*a, **k)

        return Wrapped()


def sim_client():
    return SimClient(inprocess_connector(SimWorld.seeded()))


async def flight(c):
    return (await c.call("pss", "flights.get", flight="AC1234"))["number"]


async def test_without_the_block_every_call_opens_and_closes_its_own_session(monkeypatch):
    spy, c = Spy(monkeypatch), sim_client()
    await flight(c)
    await flight(c)
    assert (spy.opened, spy.closed) == (2, 2)


async def test_inside_the_block_one_session_per_server_closed_when_it_ends(monkeypatch):
    spy, c = Spy(monkeypatch), sim_client()
    async with reuse_sessions():
        for _ in range(3):
            await flight(c)
        await c.call("occ", "flight_ops.subscribe")
        assert (spy.opened, spy.closed) == (2, 0)
    assert (spy.opened, spy.closed) == (2, 2)


async def test_sessions_are_closed_when_the_block_fails(monkeypatch):
    spy, c = Spy(monkeypatch), sim_client()
    with pytest.raises(ValueError, match="boom"):
        async with reuse_sessions():
            await flight(c)
            raise ValueError("boom")
    assert (spy.opened, spy.closed) == (1, 1)
    await flight(c)  # and outside again: a short-lived session, nothing leaked in the context
    assert (spy.opened, spy.closed) == (2, 2)


async def test_a_failed_call_evicts_its_session_and_the_next_call_reopens(monkeypatch):
    spy, c = Spy(monkeypatch, fail_first_call=True), sim_client()
    async with reuse_sessions():
        with pytest.raises(RuntimeError, match="transport broke"):
            await flight(c)
        assert await flight(c) == "AC1234"
        assert await flight(c) == "AC1234"
        assert spy.opened == 2  # the poisoned one, then one fresh session that is reused
    assert spy.closed == 2


async def test_two_connectors_never_share_a_session(monkeypatch):
    spy, a, b = Spy(monkeypatch), sim_client(), sim_client()
    async with reuse_sessions():
        await flight(a)
        await flight(b)
    assert (spy.opened, spy.closed) == (2, 2)


@pytest.mark.filterwarnings("error::ResourceWarning")
async def test_no_resource_warning_after_a_reused_run():
    c = sim_client()
    async with reuse_sessions():
        await flight(c)
    gc.collect()


# --- the tool registry is shared by the whole process, the world never is (#280) ---------------
import asyncio  # noqa: E402

from acme.sim.client import _SHARED  # noqa: E402
from acme.sim.dcs import dcs_state  # noqa: E402


async def test_two_worlds_sharing_the_registry_never_see_each_others_state():
    a, b = SimWorld.seeded(), SimWorld.seeded()
    ca, cb = SimClient(inprocess_connector(a)), SimClient(inprocess_connector(b))

    async def book(client, n):
        for _ in range(n):
            await client.call("concierge", "hotel.book", hotel_id="HT8842", nights=1, pax=1)
        await client.call("dcs", "seat.hold", flight="AC1252", seat="2A", pnr="MSLV42")

    await asyncio.gather(book(ca, 3), book(cb, 1))  # concurrent runs, same process registry
    assert (len(a.partners.hotel_bookings), len(b.partners.hotel_bookings)) == (3, 1)
    assert dcs_state(a) is not dcs_state(b)
    assert len(dcs_state(a).holds) == len(dcs_state(b).holds) == 1


async def test_a_run_never_sees_a_previous_runs_state():
    first = SimWorld.seeded()
    async with reuse_sessions():
        await SimClient(inprocess_connector(first)).call(
            "concierge", "hotel.book", hotel_id="HT8842", nights=1, pax=1
        )
    second = SimWorld.seeded()
    async with reuse_sessions():
        await SimClient(inprocess_connector(second)).call(
            "concierge", "hotel.book", hotel_id="HT8842", nights=2, pax=1
        )
    assert [b["nights"] for b in first.partners.hotel_bookings.values()] == [1]
    assert [b["nights"] for b in second.partners.hotel_bookings.values()] == [2]


async def test_the_shared_registry_holds_no_world_outside_a_session():
    await flight(sim_client())  # makes sure the registry is built
    tool = _SHARED["pss"]._tool_manager.get_tool("flights.get")  # noqa: SLF001
    with pytest.raises(RuntimeError, match="no sim world"):
        tool.fn(flight="AC1234")


# --- where the world is set and where tools run: threads and event loops ------------------------
import threading  # noqa: E402

from acme.engines.customer import _run_blocking  # noqa: E402


def book_n(world, n):
    async def go():
        client = SimClient(inprocess_connector(world))
        async with reuse_sessions():
            for _ in range(n):
                await client.call("concierge", "hotel.book", hotel_id="HT8842", nights=1, pax=1)

    return go()


def test_threads_with_their_own_event_loops_never_cross_worlds():
    """`_run_blocking` runs ops in other threads, each with its own loop: 6 parallel runs on the
    one shared registry, each must see only its own bookings."""
    worlds = [SimWorld.seeded() for _ in range(6)]
    errors = []

    def run(world, n):
        try:
            asyncio.run(book_n(world, n))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(w, i + 1)) for i, w in enumerate(worlds)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert [len(w.partners.hotel_bookings) for w in worlds] == [1, 2, 3, 4, 5, 6]


async def test_run_blocking_from_a_running_loop_uses_the_callers_world():
    """A sync op called while a loop is running goes to a pool thread with a new loop: the world
    is set there, by the session that is opened there, not inherited from this thread."""
    mine, other = SimWorld.seeded(), SimWorld.seeded()
    await book_n(other, 2)  # a different world was used on this thread before
    await asyncio.to_thread(_run_blocking, book_n(mine, 3))
    _run_blocking_in_loop = _run_blocking(book_n(mine, 1))  # pool thread, loop running here
    assert _run_blocking_in_loop is None
    assert (len(mine.partners.hotel_bookings), len(other.partners.hotel_bookings)) == (4, 2)


async def test_the_world_variable_is_restored_after_every_session():
    from acme.sim.current import CURRENT

    await book_n(SimWorld.seeded(), 1)
    with pytest.raises(LookupError):
        CURRENT.get()  # nothing leaks into the caller's context
