"""#279 / ADR 0103: a rebuild replays what the LLM actually answered (the tape), so the passenger's
history reads the same after a cross-container recovery. Offline: the deployed model is stood in
for by an LLM with different wording and a different parse."""

import json
from datetime import time

import pytest

from acme.api.engine_backend import EngineBackend
from acme.engines import customer as customer_mod
from acme.engines.customer import build_customer_engine_app
from acme.engines.sales import build_sales_engine_app
from acme.llm.fake import FakeLlm
from acme.llm.port import Acknowledge, DepartureAfter
from acme.llm.tape import MAX_ENTRIES, MAX_TEXT, RecordingLlm, TapeLlm, clean_tape
from tests.api.test_engine_mode import LocalEngine
from tests.live.agent_engine_proof import conversation

PID = "P-MARIANA"


@pytest.fixture(autouse=True)
def fake_mode(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.delenv("ACME_STATE_DB", raising=False)


class Creative(FakeLlm):
    """Stands in for the deployed model: other wording, and it reads 'after 10am' as 10:30."""

    async def compose_passenger_message(self, context):
        return "Creative: " + await super().compose_passenger_message(context)

    async def parse_passenger_request(self, text):
        if "after" in text:
            return DepartureAfter(time(10, 30))  # FakeLlm says 10:00
        return await super().parse_passenger_request(text)


class SpyEngine(LocalEngine):
    def __init__(self, app):
        super().__init__(app)
        self.calls = []

    async def call(self, op, **kwargs):
        self.calls.append((op, kwargs))
        return await super().call(op, **kwargs)

    async def stream(self, **kwargs):
        self.calls.append(("stream", kwargs))
        async for item in super().stream(**kwargs):
            yield item


def build(llm=None):
    customer = build_customer_engine_app()
    customer.set_up()
    if llm:
        customer.runtime.llm.inner = llm
    sales = build_sales_engine_app(customer_engine=customer)
    sales.set_up()
    return customer, SpyEngine(customer), SpyEngine(sales)


async def plain_run(backend, preset="happy-path"):
    state = await backend.scenario_start(preset)
    while not state["done"]:
        state = await backend.scenario_step(preset)
    return state


async def snapshot(backend):
    return (
        await backend.journeys_list(),
        conversation(await backend.api("whatsapp_messages", passenger_id=PID)),
    )


async def force_rebuild(backend, preset, step, tape, decoy="seat-taken"):
    await backend.scenario_start(decoy)  # the engine is now somewhere else
    backend._active, backend._step, backend._tape = preset, step, tape  # noqa: SLF001
    return await backend.scenario_state()


def diags(monkeypatch):
    out = []
    monkeypatch.setattr(customer_mod, "diag", lambda event, **kw: out.append((event, kw)))
    return out


async def test_a_rebuild_replays_the_texts_and_parses_that_were_really_used(monkeypatch):
    app, cust, sales = build(Creative())
    backend = EngineBackend(cust, sales)
    state = await plain_run(backend)
    plain = await snapshot(backend)
    tape = backend._tape  # noqa: SLF001
    assert any(t[1].startswith("Creative") for t in plain[1] if t[0] == "outbound")
    log = diags(monkeypatch)
    rebuilt_state = await force_rebuild(backend, "happy-path", state["step_index"], tape)
    assert rebuilt_state == state  # the parse that differs from FakeLlm kept the same path
    assert await snapshot(backend) == plain  # the history is what was sent, not fake wording
    (_, rebuild) = next(e for e in log if e[0] == "rebuild")
    assert rebuild["tape_miss"] == 0


async def test_without_the_tape_the_rebuild_still_works_and_reports_the_misses(monkeypatch):
    app, cust, sales = build(Creative())
    backend = EngineBackend(cust, sales)
    state = await plain_run(backend)
    log = diags(monkeypatch)
    for tape in (None, [], backend._tape[:1]):  # noqa: SLF001
        log.clear()
        await force_rebuild(backend, "happy-path", state["step_index"], tape)
        (_, rebuild) = next(e for e in log if e[0] == "rebuild")
        assert rebuild["tape_miss"] > 0 or tape is not None and len(tape) > 0
    assert "Creative" not in json.dumps(log)  # diag carries counts, never content


@pytest.mark.parametrize(
    "bad",
    [
        "x", {"m": "compose"}, [1], [{"m": "compose", "text": 5}],
        [{"m": "compose", "text": "x" * (MAX_TEXT + 1)}], [{"m": "other"}],
        [{"m": "compose", "text": "a", "extra": 1}], [{"m": "parse", "kind": "ack", "x": 1}],
        [{"m": "parse", "kind": "departure_after", "time": "25:99"}],
        [{"m": "parse", "kind": "nope"}], [{"m": "compose", "fail": False}],
        [{"m": "compose", "text": "a"}] * (MAX_ENTRIES + 1),
    ],
)  # fmt: skip
def test_a_malformed_tape_is_ignored_as_a_whole(bad):
    assert clean_tape(bad) is None


async def test_a_malformed_tape_does_not_fail_the_rebuild(monkeypatch):
    backend = EngineBackend(*build()[1:])
    state = await plain_run(backend)
    log = diags(monkeypatch)
    out = await force_rebuild(backend, "happy-path", state["step_index"], "garbage")
    assert out == state
    assert next(e for e in log if e[0] == "rebuild")[1]["tape_miss"] > 0


async def test_the_tape_goes_to_the_customer_engine_only_never_to_sales_or_the_api():
    app, cust, sales = build(Creative())
    backend = EngineBackend(cust, sales)
    state = await plain_run(backend)
    assert "tape" not in state  # the API client never sees it
    assert any("tape" in kw for op, kw in cust.calls if op == "scenario_state")
    assert not [c for c in sales.calls if "tape" in json.dumps(c[1], default=str)]
    assert not any("Creative" in json.dumps(c[1], default=str) for c in sales.calls)


def test_a_caller_without_a_tape_sends_no_tape_key():
    backend = EngineBackend(*build()[1:])
    backend._active, backend._step, backend._tape = "happy-path", 0, None  # noqa: SLF001
    assert backend._ctx() == {"preset": "happy-path", "step": 0}  # noqa: SLF001


async def test_recorded_failures_are_replayed_as_failures_and_misses_fall_back():
    class Boom(FakeLlm):
        async def compose_passenger_message(self, context):
            raise TimeoutError

    rec = RecordingLlm(Boom())
    with pytest.raises(TimeoutError):
        await rec.compose_passenger_message({})
    assert rec.entries == [{"m": "compose", "fail": True}]
    replay = TapeLlm(rec.entries)
    with pytest.raises(RuntimeError):
        await replay.compose_passenger_message({})
    assert "Hi" in await replay.compose_passenger_message({"passenger": "A", "summary": "s"})
    assert replay.misses == 1  # the second call had no entry: fake model, counted
    assert isinstance(await replay.parse_passenger_request("thanks"), Acknowledge)
    assert replay.misses == 2
