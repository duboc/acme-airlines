"""#279 / ADR 0103: a rebuild replays what the LLM actually answered (the tape), so the passenger's
history reads the same after a cross-container recovery. Offline: the deployed model is stood in
for by an LLM with different wording and a different parse."""

import json
from datetime import UTC, datetime, time

import pytest

from acme.api.engine_backend import EngineBackend
from acme.engines import customer as customer_mod
from acme.engines.customer import build_customer_engine_app
from acme.engines.sales import build_sales_engine_app
from acme.llm.fake import FakeLlm
from acme.llm.port import Acknowledge, DepartureAfter
from acme.llm.tape import MAX_ENTRIES, MAX_TEXT, RecordingLlm, TapeLlm, clean_tape
from tests.api.test_engine_mode import IDS, PARITY_FIELDS, LocalEngine
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
    assert rec.entries == [{"m": "compose", "fail": True, "k": "notice"}]
    replay = TapeLlm(rec.entries)
    with pytest.raises(RuntimeError):
        await replay.compose_passenger_message({})
    assert "Hi" in await replay.compose_passenger_message({"passenger": "A", "summary": "s"})
    assert replay.misses == 1  # the second call had no entry: fake model, counted
    assert isinstance(await replay.parse_passenger_request("thanks"), Acknowledge)
    assert replay.misses == 2


async def test_the_rebuild_proof_replays_the_plain_runs_tape_not_the_decoys(monkeypatch):
    """#279 live run 3: parking the engine on the decoy replaced backend._tape with the decoy's, so
    the forced rebuild replayed the wrong tape (tape_miss, every compose differed). The proof must
    put the plain run's tape back together with _active and _step."""
    from tests.live.agent_engine_proof import run_rebuild_proof

    app, cust, sales = build(Creative())
    backend = EngineBackend(cust, sales)
    log = diags(monkeypatch)

    def logs(since):
        now = datetime.now(UTC).isoformat()
        return [{"ts": now, "text": "ACME_DIAG " + json.dumps({"event": e, **kw})}
                for e, kw in log if e == "rebuild"]  # fmt: skip

    report = await run_rebuild_proof(backend, logs, "happy-path", "seat-taken", wait=0.0)
    assert report.failures == []
    (_, rebuild) = next(e for e in log if e[0] == "rebuild")
    assert rebuild["tape_miss"] == 0


class Terse(FakeLlm):
    """Live-like: the notice and the offer come back without the facts the code requires."""

    async def compose_passenger_message(self, context):
        return "Hi, so sorry about your flight. Reply here if you need anything."


async def shifted_rebuild(monkeypatch, mutate):
    app, cust, sales = build(Terse())
    backend = EngineBackend(cust, sales)
    state = await plain_run(backend)
    log = diags(monkeypatch)
    rebuilt = await force_rebuild(
        backend,
        "happy-path",
        state["step_index"],
        mutate(backend._tape),  # noqa: SLF001
    )
    return state, rebuilt, next(kw for e, kw in log if e == "rebuild")


def lose_first_parse_add_compose(tape):
    kept = [e for e in tape if e.get("kind") != "departure_after"]
    return kept + [{"m": "compose", "text": "extra", "k": "notice"}]


async def test_a_shifted_tape_matches_by_input_and_never_crashes_the_rebuild(monkeypatch):
    """#279 live run 4: a tape without the first parse used to hand the `ack` to the 'after 10am'
    request, so no offer was sent and the rebuild died with NoSentOffer."""
    state, rebuilt, diag = await shifted_rebuild(monkeypatch, lose_first_parse_add_compose)
    assert rebuilt == state
    assert diag["tape_miss"] == 1 and diag["tape_discarded"] == 0  # only the lost parse


async def test_a_tape_that_still_breaks_the_replay_is_discarded_not_fatal(monkeypatch):
    app, cust, sales = build(Terse())
    backend = EngineBackend(cust, sales)
    state = await plain_run(backend)
    log = diags(monkeypatch)
    real_step = app.scenarios.step

    async def step(**kw):  # what the live run did: the flow dies when this tape is replayed
        if any(app.runtime.llm._tape.values()):  # noqa: SLF001
            raise RuntimeError("NoSentOffer")
        return await real_step(**kw)

    monkeypatch.setattr(app.scenarios, "step", step)
    rebuilt = await force_rebuild(backend, "happy-path", state["step_index"], backend._tape)  # noqa: SLF001
    (diag,) = (kw for e, kw in log if e == "rebuild")
    assert rebuilt == state and diag["tape_discarded"] == 1


async def test_entries_match_their_own_input_and_old_unkeyed_entries_match_by_position():
    tape = [
        {"m": "parse", "kind": "ack", "k": "other-input"},
        {"m": "parse", "kind": "ack"},  # an older tape: no key
    ]
    replay = TapeLlm(tape)
    assert isinstance(await replay.parse_passenger_request("after 10am"), Acknowledge)
    assert replay.misses == 0  # the unkeyed entry, by position; the keyed one is not hers
    assert not isinstance(await replay.parse_passenger_request("hello"), Acknowledge)  # fake
    assert replay.misses == 1
    assert clean_tape([{"m": "parse", "kind": "ack", "k": ""}]) is None
    assert clean_tape([{"m": "parse", "kind": "ack", "k": 5}]) is None


async def test_the_rebuild_proof_reports_only_the_error_class(monkeypatch):
    from tests.live.agent_engine_proof import run_rebuild_proof

    class Leaky(EngineBackend):
        async def scenario_state(self):
            raise RuntimeError("Request Data: {'tape': ['passenger text']}")

    app, cust, sales = build(Creative())
    report = await run_rebuild_proof(Leaky(cust, sales), lambda since: [], wait=0.0)
    text = report.markdown() + str(report.failures)
    assert (
        "RuntimeError" in text
        and "passenger text" not in text
        and "tape" not in text.replace("forced rebuild", "").replace("replay tape", "")
    )


async def test_manual_whatsapp_turns_matching_scripted_steps_survive_rebuild(monkeypatch):
    """#322 AC2 & AC3: manual reply and action matching scripted preset steps advance _step,
    refresh _tape, and survive a cross-container _ensure_scenario rebuild with tape_miss == 0."""
    app, cust, sales = build(Creative())
    backend = EngineBackend(cust, sales)
    await backend.scenario_start("happy-path")
    for _ in range(3):
        await backend.scenario_step("happy-path")
    await backend.api("whatsapp_reply", passenger_id=PID, text="Perfect, thanks!")
    assert backend._step == 4  # noqa: SLF001
    assert any(e.get("k") == "offer" for e in (backend._tape or []))  # noqa: SLF001
    await backend.api("whatsapp_action", passenger_id=PID, action="accept_offer")
    assert backend._step == 5 and backend.done  # noqa: SLF001
    before_state = await backend.scenario_state()
    before_snap = await snapshot(backend)
    log = diags(monkeypatch)
    rebuilt_state = await force_rebuild(backend, "happy-path", backend._step, backend._tape)  # noqa: SLF001
    assert rebuilt_state == before_state
    assert rebuilt_state["offer_status"] == "accepted" and rebuilt_state["charged"] == "135.00"
    assert await snapshot(backend) == before_snap
    (_, rebuild) = next(e for e in log if e[0] == "rebuild")
    assert rebuild["tape_miss"] == 0


async def test_adhoc_manual_whatsapp_turns_survive_cross_container_rebuild(monkeypatch):
    """#322 AC2 & AC3: ad-hoc manual WhatsApp replies and button taps are recorded on the tape and
    replayed across cold and same-step mismatched containers without losing state."""
    app, cust, sales = build(Creative())
    backend = EngineBackend(cust, sales)
    await backend.scenario_start("happy-path")
    for _ in range(3):
        await backend.scenario_step("happy-path")
    # Ad-hoc acknowledgement text (does not match steps[3].text == "Perfect, thanks!").
    await backend.api("whatsapp_reply", passenger_id=PID, text="Sounds good, thank you!")
    assert backend._step == 3  # noqa: SLF001
    assert any(e.get("m") == "manual_reply" for e in (backend._tape or []))  # noqa: SLF001
    assert any(e.get("k") == "offer" for e in (backend._tape or []))  # noqa: SLF001
    await backend.api("whatsapp_action", passenger_id=PID, action="accept_offer")
    assert any(e.get("m") == "manual_action" for e in (backend._tape or []))  # noqa: SLF001
    before_state = await backend.scenario_state()
    before_snap = await snapshot(backend)
    (jid,) = [j["journey_id"] for j in before_snap[0]]
    before_detail = await backend.journey_detail(jid)
    assert before_detail["resolution_acknowledged"] is True
    assert before_detail["status"] == "CLOSED" and before_detail["seat"]["seat"] == "2A"

    # 1) Cold / decoy container rebuild:
    log = diags(monkeypatch)
    rebuilt_state = await force_rebuild(backend, "happy-path", backend._step, backend._tape)  # noqa: SLF001
    assert rebuilt_state == before_state
    assert await snapshot(backend) == before_snap
    rebuilt_detail = await backend.journey_detail(jid)
    assert rebuilt_detail["resolution_acknowledged"] is True
    assert rebuilt_detail["status"] == "CLOSED" and rebuilt_detail["seat"]["seat"] == "2A"
    assert rebuilt_detail["offer"]["status"] == "accepted"
    (_, rebuild) = next(e for e in log if e[0] == "rebuild")
    assert rebuild["tape_miss"] == 0

    # 2) Same-step container parked at ("happy-path", 3) BEFORE the ad-hoc manual turns:
    saved_tape = list(backend._tape or [])  # noqa: SLF001
    await backend.scenario_start("happy-path")
    for _ in range(3):
        await backend.scenario_step("happy-path")
    log.clear()
    backend._tape = saved_tape  # noqa: SLF001
    rebuilt_same_step = await backend.scenario_state()
    assert rebuilt_same_step == before_state
    assert next(e for e in log if e[0] == "rebuild")[1]["reason"] == "step_mismatch"


def test_clean_tape_validates_manual_turn_entries():
    valid = [
        {"m": "manual_reply", "pid": "P-MARIANA", "text": "Thanks!", "step": 3.0},
        {"m": "manual_action", "pid": "P-MARIANA", "action": "accept_offer", "step": 3},
    ]
    cleaned = clean_tape(valid)
    assert cleaned is not None and cleaned[0]["step"] == 3 and isinstance(cleaned[0]["step"], int)
    for bad in (
        [{"m": "manual_reply", "pid": "", "text": "x", "step": 0}],
        [{"m": "manual_reply", "pid": "P-MARIANA", "text": "x", "step": -1}],
        [{"m": "manual_reply", "pid": "P-MARIANA", "text": "x", "step": 1.5}],
        [{"m": "manual_action", "pid": "P-MARIANA", "action": "bogus", "step": 0}],
    ):
        assert clean_tape(bad) is None


@pytest.mark.parametrize("sid", IDS)
async def test_all_six_presets_preserve_structural_and_seat_reason_parity_after_tape_rebuild(sid):
    _, cust, sales = build(Creative())
    backend = EngineBackend(cust, sales)
    state = await plain_run(backend, sid)
    decoy = "happy-path" if sid == "seat-taken" else "seat-taken"
    rebuilt = await force_rebuild(backend, sid, state["step_index"], backend._tape, decoy=decoy)  # noqa: SLF001
    assert {k: rebuilt[k] for k in PARITY_FIELDS} == {k: state[k] for k in PARITY_FIELDS}
    if sid == "seat-taken":
        assert rebuilt["seat"] == "15A" and rebuilt["seat_reason"]
        assert "15A" in rebuilt["seat_reason"]
