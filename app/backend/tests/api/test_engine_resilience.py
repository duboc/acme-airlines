"""#306: a transient Agent Engine error is retried when the op is a read and otherwise a clean 503,
never a raw 500; a step that committed stays consistent when its Sales tick or state read fails."""

import pytest

from acme.api import engine_backend
from acme.api.engine_backend import (
    EngineBackend,
    EngineUnavailable,
    StepApplied,
    _AdcEngine,
    create_engine_app,
)
from tests.api.test_engine_mode import LocalEngine, client
from tests.api.test_engine_mode import engine_backend as local_backend


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(engine_backend, "RETRY_BACKOFF", (0.0, 0.0))


class Flaky:
    """The remote engine object: `fail` errors first, then answers."""

    def __init__(self, fail=0, exc=None):
        self.fail, self.calls = fail, []
        self.exc = exc or ConnectionError("rpc unavailable")

    def _maybe_fail(self, op):
        self.calls.append(op)
        if len([c for c in self.calls if c == op]) <= self.fail:
            raise self.exc

    def scenario_state(self, **kw):
        self._maybe_fail("scenario_state")
        return {"step_index": 3}

    def scenario_start(self, **kw):
        self._maybe_fail("scenario_start")
        return {"step_index": 0}

    async def async_stream_query(self, **kw):
        self._maybe_fail("stream")
        yield {"ok": True}


def adc(remote):
    engine = _AdcEngine("projects/p/locations/l/reasoningEngines/1")
    engine._engine = remote  # noqa: SLF001 - already resolved: no credentials needed
    return engine


async def test_given_a_transient_error_on_a_read_when_it_is_retried_then_it_succeeds():
    remote = Flaky(fail=2)
    assert await adc(remote).call("scenario_state") == {"step_index": 3}
    assert remote.calls == ["scenario_state"] * 3


async def test_given_a_persistent_error_on_a_read_then_it_is_bounded_and_unavailable():
    remote = Flaky(fail=99)
    with pytest.raises(EngineUnavailable) as err:
        await adc(remote).call("scenario_state")
    assert len(remote.calls) == 3  # one try and two retries
    assert "ConnectionError" in str(err.value) and "rpc unavailable" not in str(err.value)


async def test_given_an_error_on_a_write_then_it_is_never_repeated():
    remote = Flaky(fail=1)
    with pytest.raises(EngineUnavailable):
        await adc(remote).call("scenario_start", scenario_id="happy-path")
    assert remote.calls == ["scenario_start"]  # it may have taken effect: no second try


async def test_given_a_failing_stream_then_it_is_unavailable_not_a_raw_error():
    with pytest.raises(EngineUnavailable):
        async for _ in adc(Flaky(fail=1)).stream(message="tick", user_id="demo"):
            pass


async def test_given_a_persistent_engine_error_over_http_then_the_answer_is_a_json_503():
    class Down(LocalEngine):
        async def call(self, op, **kwargs):
            raise EngineUnavailable("The Agent Engine call failed (X); try again.")

    app = create_engine_app(EngineBackend(Down(None), Down(None)))
    async with client(app) as c:
        r = await c.get("/journeys")
    assert r.status_code == 503 and r.json() == {
        "detail": "The Agent Engine call failed (X); try again."
    }


async def test_given_a_failing_sales_tick_after_a_committed_step_then_the_step_still_answers():
    backend = local_backend()

    class BrokenSales:
        def stream(self, **kw):
            raise RuntimeError("tick rpc failed")

        async def call(self, op, **kw):
            raise RuntimeError

    backend._sales = BrokenSales()  # noqa: SLF001
    app = create_engine_app(backend)
    async with client(app) as c:
        await c.post("/scenarios/happy-path/start")
        r = await c.post("/scenarios/happy-path/step")
        assert r.status_code == 200 and r.json()["step_index"] == 1
        assert backend._step == 1  # noqa: SLF001 - matches the engine
        assert (await c.post("/scenarios/happy-path/step")).json()["step_index"] == 2


async def test_given_a_state_read_failing_after_a_committed_step_then_it_says_the_step_applied():
    backend = local_backend()
    real, stepped = backend._customer.call, []  # noqa: SLF001

    async def call(op, **kw):
        if op == "scenario_state" and stepped:  # the read that follows the committed step
            raise EngineUnavailable("down")
        out = await real(op, **kw)
        if op == "scenario_step":
            stepped.append(True)
        return out

    backend._customer.call = call  # noqa: SLF001
    app = create_engine_app(backend)
    async with client(app) as c:
        await c.post("/scenarios/happy-path/start")
        r = await c.post("/scenarios/happy-path/step")
    assert r.status_code == 503 and "step was applied" in r.json()["detail"]
    assert backend._step == 1  # noqa: SLF001 - the counter follows the committed step
    assert issubclass(StepApplied, EngineUnavailable)


# --- #335: the query quota -------------------------------------------------------------------


class QuotaExhausted(Exception):
    """What grpc/api_core raise for RESOURCE_EXHAUSTED (matched by name, like the real one)."""


QuotaExhausted.__name__ = "ResourceExhausted"


async def test_given_the_quota_is_exhausted_on_a_read_then_it_is_a_429_with_retry_after_unretried():
    remote = Flaky(fail=99, exc=QuotaExhausted("429 quota"))
    with pytest.raises(engine_backend.EngineQuotaExceeded) as err:
        await adc(remote).call("scenario_state")
    assert remote.calls == ["scenario_state"]  # a retry would burn more quota
    assert "quota" in str(err.value) and "429" not in str(err.value)


async def test_given_a_grpc_status_code_for_exhaustion_then_it_is_a_quota_error():
    class Rpc(Exception):
        def code(self):
            return "StatusCode.RESOURCE_EXHAUSTED"

    with pytest.raises(engine_backend.EngineQuotaExceeded):
        await adc(Flaky(fail=1, exc=Rpc())).call("scenario_start", scenario_id="x")


async def test_given_the_quota_is_exhausted_over_http_then_429_retry_after_and_no_detail_leak():
    class Quota(LocalEngine):
        async def call(self, op, **kwargs):
            raise engine_backend.EngineQuotaExceeded("The Agent Engine query quota is exhausted.")

    app = create_engine_app(EngineBackend(Quota(None), Quota(None)))
    async with client(app) as c:
        reset, journeys = await c.post("/scenario/reset"), await c.get("/journeys")
    for r in (reset, journeys):
        assert r.status_code == 429 and r.headers["retry-after"] == "20"
        assert r.json() == {"detail": "The Agent Engine query quota is exhausted."}


async def test_given_an_unavailable_engine_over_http_then_503_with_retry_after():
    class Down(LocalEngine):
        async def call(self, op, **kwargs):
            raise EngineUnavailable("down")

    async with client(create_engine_app(EngineBackend(Down(None), Down(None)))) as c:
        r = await c.post("/scenario/reset")
    assert r.status_code == 503 and r.headers["retry-after"] == "5"


class CountingEngine:
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    async def call(self, op, **kwargs):
        self.calls.append(op)
        return await self.inner.call(op, **kwargs)

    def stream(self, **kw):
        return self.inner.stream(**kw)


@pytest.fixture
def clock(monkeypatch):
    from types import SimpleNamespace

    now = [1000.0]  # a stub module: patching `time.monotonic` itself would stop the event loop
    monkeypatch.setattr(engine_backend, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


async def test_given_many_streams_then_the_engine_is_asked_once_per_interval(clock):
    backend = local_backend()
    counter = backend._customer = CountingEngine(backend._customer)  # noqa: SLF001
    await backend.scenario_start("happy-path")
    await backend.scenario_step("happy-path")
    (jid,) = [j["journey_id"] for j in await backend.journeys_list()]
    counter.calls.clear()
    for _ in range(20):  # twenty streams polling in the same instant
        await backend.timeline(jid)
    assert counter.calls.count("journey_events") == 1


async def test_given_one_idle_stream_then_the_engine_rate_stays_low_and_backs_off(clock):
    backend = local_backend()
    counter = backend._customer = CountingEngine(backend._customer)  # noqa: SLF001
    await backend.scenario_start("happy-path")
    await backend.scenario_step("happy-path")
    (jid,) = [j["journey_id"] for j in await backend.journeys_list()]
    await backend.timeline(jid)  # the first look finds the events so far
    counter.calls.clear()
    for _ in range(30):  # one minute of a stream looking every POLL_SECONDS (2 s)
        clock[0] += engine_backend.POLL_SECONDS
        await backend.timeline(jid)
    per_minute = counter.calls.count("journey_events")
    assert per_minute <= 30  # it was 120 a minute per stream at 0.5 s
    counter.calls.clear()
    for _ in range(30):  # the next minute is idle too: the interval has grown
        clock[0] += engine_backend.POLL_SECONDS
        await backend.timeline(jid)
    assert counter.calls.count("journey_events") <= 12


async def test_given_a_write_then_the_open_streams_look_again_at_once(clock):
    backend = local_backend()
    await backend.scenario_start("happy-path")
    await backend.scenario_step("happy-path")
    await backend.scenario_step("happy-path")
    (jid,) = [j["journey_id"] for j in await backend.journeys_list()]
    n = len(await backend.timeline(jid))
    for _ in range(3):  # idle: the interval grows
        clock[0] += engine_backend.POLL_SECONDS
        await backend.timeline(jid)
    await backend.scenario_step("happy-path")  # a step: new events
    assert len(await backend.timeline(jid)) > n  # seen without waiting for the interval


async def test_given_reconnects_then_the_journeys_list_is_queried_once_per_ttl(clock):
    backend = local_backend()
    counter = backend._customer = CountingEngine(backend._customer)  # noqa: SLF001
    for _ in range(10):
        await backend.known_journeys()
    assert counter.calls.count("journeys_list") == 1
    clock[0] += engine_backend.JOURNEYS_TTL_SECONDS
    await backend.known_journeys()
    assert counter.calls.count("journeys_list") == 2


async def test_given_a_quota_error_in_a_stream_then_it_sends_a_retry_hint_and_ends_cleanly():
    from acme.api.sse import event_stream

    class Request:
        async def is_disconnected(self):
            return False

    async def fetch(sent):
        raise engine_backend.EngineQuotaExceeded("quota")

    frames = [f async for f in event_stream(Request(), fetch, limit=None, poll_seconds=0.001)]
    assert frames == ["retry: 3000\n\n", "retry: 20000\n\n"]


async def test_given_a_stream_error_that_is_not_typed_then_it_still_surfaces():
    from acme.api.sse import event_stream

    class Request:
        async def is_disconnected(self):
            return False

    async def fetch(sent):
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        [f async for f in event_stream(Request(), fetch, limit=None, poll_seconds=0.001)]


# --- #351 and #356: the Sales tick of a step, and the timeline poller ---------------------------


class ScriptedSales:
    """A Sales engine whose tick fails `fail` times, then runs the real one."""

    def __init__(self, real, fail=0, exc=None):
        self.real, self.fail, self.calls = real, fail, 0
        self.exc = exc or RuntimeError("tick rpc failed")

    def stream(self, **kw):
        self.calls += 1
        if self.fail > 0:
            self.fail -= 1
            raise self.exc
        return self.real.stream(**kw)

    async def call(self, op, **kw):
        return await self.real.call(op, **kw)


async def to_step(backend, n):
    await backend.scenario_start("happy-path")
    for _ in range(n):
        await backend.scenario_step("happy-path")


async def test_given_a_transient_sales_failure_at_the_ack_step_then_one_retry_sends_the_offer():
    backend = local_backend()
    await to_step(backend, 3)
    backend._sales = sales = ScriptedSales(backend._sales, fail=1)  # noqa: SLF001
    state = await backend.scenario_step("happy-path")  # the ACK step
    assert sales.calls == 2 and state["offer_status"] == "sent"  # retried once, offer delivered
    assert not backend._sales_pending  # noqa: SLF001


async def test_given_sales_down_at_the_ack_step_then_the_accept_step_waits_instead_of_wedging():
    """#351: the swallowed tick left no offer, step 5 raised NoSentOffer inside the engine (503)
    on every click, forever."""
    backend = local_backend()
    await to_step(backend, 3)
    real_sales = backend._sales  # noqa: SLF001
    backend._sales = down = ScriptedSales(real_sales, fail=99)  # noqa: SLF001
    ack = await backend.scenario_step("happy-path")
    assert ack["step_index"] == 4 and ack["offers_sent"] == 0 and backend._sales_pending  # noqa: SLF001
    engine_calls = []
    real_call = backend._customer.call  # noqa: SLF001

    async def spy(op, **kw):
        engine_calls.append(op)
        return await real_call(op, **kw)

    backend._customer.call = spy  # noqa: SLF001
    with pytest.raises(EngineUnavailable) as err:  # still down: no Customer step, no NoSentOffer
        await backend.scenario_step("happy-path")
    assert "Sales engine could not be reached" in str(err.value) and not isinstance(
        err.value, StepApplied
    )
    assert "scenario_step" not in engine_calls and backend._step == 4  # noqa: SLF001
    assert down.calls >= 3  # the catch-up tried (with its retry) on the second click too
    down.fail = 0  # Sales recovers: the catch-up tick proposes the offer, the accept goes through
    state = await backend.scenario_step("happy-path")
    assert state["offer_status"] == "accepted" and state["charged"] == "135.00"


async def test_given_a_quota_error_on_the_tick_then_it_is_not_retried():
    backend = local_backend()
    await to_step(backend, 1)
    backend._sales = sales = ScriptedSales(  # noqa: SLF001
        backend._sales,
        fail=99,
        exc=engine_backend.EngineQuotaExceeded("quota"),  # noqa: SLF001
    )
    await backend.scenario_step("happy-path")
    assert sales.calls == 1 and backend._sales_pending  # noqa: SLF001


async def test_given_a_write_then_the_streams_are_stirred_after_the_sales_tick_not_before():
    backend = local_backend()
    await to_step(backend, 1)
    seen = {}
    real = backend._sales  # noqa: SLF001

    class Probe(ScriptedSales):
        def stream(self, **kw):
            seen["seq_during_tick"] = backend._stir_seq  # noqa: SLF001
            return super().stream(**kw)

    backend._sales = Probe(real)  # noqa: SLF001
    before = backend._stir_seq  # noqa: SLF001
    await backend.scenario_step("happy-path")
    assert seen["seq_during_tick"] == before  # nothing was stirred while Sales was working
    assert backend._stir_seq == before + 1  # noqa: SLF001 - once, when the whole step is done


async def test_given_a_poll_in_flight_when_a_write_stirs_then_the_stir_is_not_overwritten(clock):
    backend = local_backend()
    await to_step(backend, 1)
    (jid,) = [j["journey_id"] for j in await backend.journeys_list()]
    real = backend.journey_events

    async def poll_during_a_write(journey_id, since=0):
        out = await real(journey_id, since)
        backend._stir()  # a step, reply or action lands while this poll is in flight
        return out

    backend.journey_events = poll_during_a_write
    await backend.timeline(jid)
    at, _, interval = backend._timelines[(backend.run, jid)]  # noqa: SLF001
    assert at == float("-inf") and interval == engine_backend.TIMELINE_BASE_SECONDS


async def test_given_a_step_or_a_detail_then_no_redundant_engine_query_is_made():
    backend = local_backend()
    counter = backend._customer = CountingEngine(backend._customer)  # noqa: SLF001
    app = create_engine_app(backend)
    async with client(app) as c:
        await c.post("/scenarios/happy-path/start")
        counter.calls.clear()
        r = await c.post("/scenarios/happy-path/step")
        assert r.status_code == 200
        assert counter.calls.count("scenario_state") == 1  # the one that answers; none before
        (jid,) = [j["journey_id"] for j in (await c.get("/journeys")).json()]
        counter.calls.clear()
        for _ in range(3):
            assert (await c.get(f"/journeys/{jid}")).status_code == 200
        assert counter.calls.count("journeys_list") <= 1  # cached, not one per request
        while not backend.done:
            await c.post("/scenarios/happy-path/step")
        assert (await c.post("/scenarios/happy-path/step")).status_code == 409  # finished, no RPC


# --- #372: every step after the acknowledgement waits for the Sales tick ------------------


async def test_given_sales_down_at_the_ack_step_then_the_replan_step_of_upgrade_race_waits():
    """#372: only accept/decline were held back, so step 5 (replan, which clears the
    acknowledgement) ran while the offer did not exist, and step 6 wedged with NoSentOffer."""
    backend = local_backend()
    await backend.scenario_start("upgrade-race")
    for _ in range(3):
        await backend.scenario_step("upgrade-race")
    backend._sales = down = ScriptedSales(backend._sales, fail=99)  # noqa: SLF001
    ack = await backend.scenario_step("upgrade-race")
    assert ack["offers_sent"] == 0 and backend._sales_pending  # noqa: SLF001
    calls, real_call = [], backend._customer.call  # noqa: SLF001

    async def spy(op, **kw):
        calls.append((op, kw))
        return await real_call(op, **kw)

    backend._customer.call = spy  # noqa: SLF001
    with pytest.raises(EngineUnavailable):  # the replan step must not run yet
        await backend.scenario_step("upgrade-race")
    assert not [c for c in calls if c[0] == "scenario_step"] and backend._step == 4  # noqa: SLF001
    down.fail = 0  # Sales recovers
    state = await backend.scenario_step("upgrade-race")  # the replan step, now with an offer
    assert state["step_index"] == 5 and state["offer_status"] == "invalidated"
    final = await backend.scenario_step("upgrade-race")  # the stale accept: refused, not wedged
    assert final["done"] and final["offer_status"] == "invalidated" and final["charged"] == "0.00"


async def test_given_a_catch_up_tick_then_the_tape_sent_with_the_next_step_has_the_offer_text():
    """#372: the tape was not refreshed after the catch-up tick, so a step landing on another
    replica rebuilt without the offer's text (tape_miss)."""
    backend = local_backend()
    await to_step(backend, 3)
    backend._sales = down = ScriptedSales(backend._sales, fail=99)  # noqa: SLF001
    await backend.scenario_step("happy-path")  # the ACK step: the tick fails
    down.fail = 0
    seen = []
    real_call = backend._customer.call  # noqa: SLF001

    async def spy(op, **kw):
        if op == "scenario_step":
            seen.append(kw.get("tape"))
        return await real_call(op, **kw)

    backend._customer.call = spy  # noqa: SLF001
    await backend.scenario_step("happy-path")  # the accept
    assert seen and any(e.get("k") == "offer" for e in seen[0])


async def test_given_sales_down_before_the_acknowledgement_then_steps_still_run():
    backend = local_backend()
    await backend.scenario_start("happy-path")
    backend._sales = ScriptedSales(backend._sales, fail=99)  # noqa: SLF001
    for expected in (1, 2, 3):  # cancel, notify, reply: no offer is due yet
        assert (await backend.scenario_step("happy-path"))["step_index"] == expected


# --- #374: Agent Engine returns every number as a double ----------------------------------------


def as_doubles(value):
    """What protobuf Struct does to a JSON reply: every number (not a bool) becomes a float."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, dict):
        return {k: as_doubles(v) for k, v in value.items()}
    if isinstance(value, list):
        return [as_doubles(v) for v in value]
    return value


class DoublesEngine:
    def __init__(self, inner):
        self.inner = inner

    async def call(self, op, **kwargs):
        return as_doubles(await self.inner.call(op, **kwargs))

    def stream(self, **kwargs):
        return self.inner.stream(**kwargs)


async def test_every_preset_steps_through_http_when_the_engine_replies_with_doubles():
    """#374: `steps[self._step]` raised `TypeError: tuple indices must be integers, not float` on
    the deployed engine, so every step was a 500; the CI fakes returned ints."""
    from acme.api.scenarios import BY_ID

    for sid in BY_ID:
        backend = local_backend()
        backend._customer = DoublesEngine(backend._customer)  # noqa: SLF001
        async with client(create_engine_app(backend)) as c:
            state = (await c.post(f"/scenarios/{sid}/start")).json()
            while not state["done"]:
                r = await c.post(f"/scenarios/{sid}/step")
                assert r.status_code == 200, (sid, r.text)
                state = r.json()
                assert isinstance(backend._step, int) and not isinstance(backend._step, bool)  # noqa: SLF001
            assert backend.done and backend._step == len(BY_ID[sid].steps)  # noqa: SLF001


async def test_a_step_index_that_is_not_whole_is_an_engine_fault_not_a_500():
    class Odd(LocalEngine):
        async def call(self, op, **kwargs):
            return {"step_index": 2.5}

    backend = EngineBackend(Odd(None), Odd(None))
    with pytest.raises(EngineUnavailable):
        await backend.scenario_start("happy-path")
    assert engine_backend.whole(5.0) == 5 and isinstance(engine_backend.whole(5.0), int)
    for bad in (True, "5", None, 5.5):
        with pytest.raises(EngineUnavailable):
            engine_backend.whole(bad)


async def test_an_api_error_from_the_engine_keeps_an_integer_http_status_over_asgi():
    """#327: the engine's `{"error": {"status": 409.0}}` reached h11 as a float ("status code must
    be integer") and the demo answered 500 to a stale Accept tap."""
    backend = local_backend()
    backend._customer = DoublesEngine(backend._customer)  # noqa: SLF001
    async with client(create_engine_app(backend)) as c:
        await c.post("/scenarios/happy-path/start")  # no offer has been sent yet
        r = await c.post("/whatsapp/P-MARIANA/actions", json={"action": "accept_offer"})
    assert isinstance(r.status_code, int) and r.status_code == 409
    assert r.json()["detail"]


def test_ok_turns_a_double_status_into_an_integer_api_error():
    from acme.api.errors import ApiError

    with pytest.raises(ApiError) as err:
        engine_backend.ok({"error": {"status": 409.0, "detail": "no offer", "code": "x"}})
    assert err.value.status_code == 409 and isinstance(err.value.status_code, int)
