"""#305: the journey event stream stops when the client is gone, when its run is over and after a
bounded time, and says so while idle. Polling an engine for a dead client cost money and, after a
scenario restart, polled with another run's context (a `journey not found` storm)."""

import asyncio
import json

import pytest

from acme.api import sse
from acme.api.engine_backend import create_engine_app
from acme.api.sse import KEEPALIVE, event_stream
from tests.api.test_engine_mode import client, engine_backend


class Request:
    """A request whose client leaves after `leave_after` checks."""

    def __init__(self, leave_after=None):
        self.checks, self.leave_after = 0, leave_after

    async def is_disconnected(self):
        self.checks += 1
        return self.leave_after is not None and self.checks > self.leave_after


class Fetch:
    def __init__(self, events=()):
        self.calls, self.events = 0, list(events)

    async def __call__(self, sent):
        self.calls += 1
        return self.events[sent:]


async def _collect(stream):
    return [frame async for frame in stream]


async def drain(stream, timeout=5):
    return await asyncio.wait_for(_collect(stream), timeout)


async def test_given_a_client_that_leaves_when_idle_then_polling_stops_within_a_bounded_time():
    fetch = Fetch()
    request = Request(leave_after=3)
    stream = event_stream(request, fetch, limit=None, poll_seconds=0.001)
    assert await drain(stream) == []  # ended by itself
    calls = fetch.calls
    await asyncio.sleep(0.05)
    assert fetch.calls == calls and calls <= 4  # and nothing polls afterwards


async def test_given_an_idle_stream_then_a_keep_alive_comment_is_sent(monkeypatch):
    monkeypatch.setattr(sse, "SSE_KEEPALIVE_SECONDS", 0.01)
    stream = event_stream(Request(), Fetch(), limit=None, poll_seconds=0.001)
    first = await asyncio.wait_for(anext(stream), 2)
    assert first == KEEPALIVE == ": keep-alive\n\n"
    await stream.aclose()


async def test_given_events_arriving_then_no_keep_alive_is_mixed_in(monkeypatch):
    monkeypatch.setattr(sse, "SSE_KEEPALIVE_SECONDS", 0.01)
    events = [{"n": 1}, {"n": 2}]
    stream = event_stream(Request(), Fetch(events), limit=2, poll_seconds=0.001)
    assert await drain(stream) == [f"data: {json.dumps(e)}\n\n" for e in events]


async def test_given_the_lifetime_cap_when_reached_then_the_stream_ends_cleanly(monkeypatch):
    monkeypatch.setattr(sse, "SSE_MAX_SECONDS", 0.05)
    stream = event_stream(Request(), Fetch(), limit=None, poll_seconds=0.001)
    assert all(frame == KEEPALIVE for frame in await drain(stream))
    assert sse.SSE_MAX_SECONDS < 300  # the real cap sits under the Cloud Run request timeout


async def test_given_a_limit_when_events_arrive_then_exactly_that_many_frames_are_sent():
    fetch = Fetch([{"n": i} for i in range(5)])
    frames = await drain(event_stream(Request(), fetch, limit=3, poll_seconds=0.001))
    assert [json.loads(f[6:])["n"] for f in frames] == [0, 1, 2]


async def test_given_the_run_ended_then_the_stream_stops_without_calling_the_engine_again():
    fetch, open_ = Fetch(), [True]
    stream = event_stream(
        Request(), fetch, limit=None, poll_seconds=0.001, still_open=lambda: open_[0]
    )
    task = asyncio.create_task(_collect(stream))
    await asyncio.sleep(0.02)
    open_[0] = False
    assert await asyncio.wait_for(task, 2) == []
    calls = fetch.calls
    await asyncio.sleep(0.02)
    assert fetch.calls == calls


async def test_given_a_call_failing_because_the_run_ended_then_it_is_not_an_error():
    open_ = [True]

    async def fetch(sent):
        open_[0] = False  # the restart lands while the call is in flight
        raise RuntimeError("journey not found")

    stream = event_stream(Request(), fetch, limit=None, poll_seconds=0.001,
                          still_open=lambda: open_[0])  # fmt: skip
    assert await drain(stream) == []


async def test_given_a_failure_of_the_live_run_then_it_still_surfaces():
    with pytest.raises(RuntimeError):
        await drain(event_stream(Request(), _boom, limit=None, poll_seconds=0.001))


async def _boom(sent):
    raise RuntimeError("engine down")


async def test_given_a_scenario_restart_then_the_old_engine_stream_ends(monkeypatch):
    backend = engine_backend()
    app = create_engine_app(backend)
    calls = []
    real = backend.journey_events

    async def spy(journey_id, since=0):
        calls.append(journey_id)
        return await real(journey_id, since)

    monkeypatch.setattr(backend, "journey_events", spy)
    monkeypatch.setattr("acme.api.engine_backend.POLL_SECONDS", 0.01)
    async with client(app) as c:
        await c.post("/scenarios/happy-path/start")
        await c.post("/scenarios/happy-path/step")
        (jid,) = [j["journey_id"] for j in (await c.get("/journeys")).json()]
        task = asyncio.create_task(c.get(f"/journeys/{jid}/events"))
        await asyncio.sleep(0.3)
        assert not task.done() and calls  # an open stream, polling
        await c.post("/scenarios/seat-taken/start")  # a new run
        response = await asyncio.wait_for(task, 5)  # the old stream ended by itself
        assert response.status_code == 200
        before = len(calls)
        await asyncio.sleep(0.2)
        assert len(calls) == before  # no polling with the new run's context


async def test_given_a_scenario_restart_then_the_old_local_stream_ends():
    from acme.api.app import create_app

    async with client(create_app()) as c:
        await c.post("/scenarios/happy-path/start")
        await c.post("/scenarios/happy-path/step")
        (jid,) = [j["journey_id"] for j in (await c.get("/journeys")).json()]
        task = asyncio.create_task(c.get(f"/journeys/{jid}/events"))
        await asyncio.sleep(0.3)
        assert not task.done()
        await c.post("/scenarios/seat-taken/start")  # replaces the journeys of the old run
        assert (await asyncio.wait_for(task, 5)).status_code == 200
