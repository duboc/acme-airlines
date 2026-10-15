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
