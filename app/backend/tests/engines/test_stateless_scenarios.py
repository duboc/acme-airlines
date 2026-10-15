"""#266 part 2: scenario ops do not depend on container-local state (ADR 0103). Containers are
separate engine apps with NO shared state, called round-robin; the caller passes preset and step."""

import logging

import pytest

from acme import metrics, telemetry
from acme.engines.customer import build_customer_engine_app
from tests.api.test_engine_mode import STRUCTURAL
from tests.engines.test_process_identity import Lines

PID = "P-MARIANA"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("ACME_STATE_DB", raising=False)
    monkeypatch.delenv("ENGINE_MODE", raising=False)


async def test_a_container_that_never_saw_the_scenario_rebuilds_instead_of_failing():
    a, b = (build_customer_engine_app() for _ in range(2))
    a.set_up()
    b.set_up()
    a.scenario_start("happy-path")
    for step in range(3):
        await a.scenario_step(preset="happy-path", step=step)
    state_a = a.scenario_state(preset="happy-path", step=3)
    state_b = b.scenario_state(preset="happy-path", step=3)  # b has no scenario at all
    assert {k: state_b[k] for k in STRUCTURAL} == {k: state_a[k] for k in STRUCTURAL}
    nxt_a = await a.scenario_step(preset="happy-path", step=3)
    nxt_b = await b.scenario_step(preset="happy-path", step=3)
    assert {k: nxt_b[k] for k in STRUCTURAL} == {k: nxt_a[k] for k in STRUCTURAL}


async def test_a_stale_local_step_is_replaced_by_the_callers_step():
    a = build_customer_engine_app()
    a.set_up()
    a.scenario_start("happy-path")
    await a.scenario_step(preset="happy-path", step=0)
    await a.scenario_step(preset="happy-path", step=1)  # a is at step 2
    state = a.scenario_state(preset="happy-path", step=1)  # the caller says step 1
    assert state["step_index"] == 1
    assert a.scenario_state(preset="declines-upgrade", step=0)["scenario_id"] == "declines-upgrade"


def test_callers_without_preset_and_step_behave_as_before():
    a = build_customer_engine_app()
    a.set_up()
    with pytest.raises(Exception, match="no scenario is started"):
        a.scenario_state()
    a.scenario_start("happy-path")
    assert a.scenario_state()["step_index"] == 0


async def test_a_rebuild_is_logged_with_the_container_identity():
    h = Lines()
    logger = logging.getLogger("acme.diag")
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    try:
        a, b = (build_customer_engine_app() for _ in range(2))
        a.set_up()
        b.set_up()
        a.scenario_start("happy-path")
        await a.scenario_step(preset="happy-path", step=0)
        b.scenario_state(preset="happy-path", step=1)
    finally:
        logger.removeHandler(h)
    (rebuild,) = [r for r in h.records if r["event"] == "rebuild"]
    ident = telemetry.process_identity()
    assert (rebuild["preset"], rebuild["to_step"], rebuild["reason"]) == (
        "happy-path", 1, "no_local_scenario"
    )  # fmt: skip
    assert rebuild["from_step"] is None
    assert rebuild["proc"] == ident["proc"] and rebuild["container"] == ident["container"]
    assert isinstance(rebuild["ms"], (int, float))


async def test_a_rebuild_does_not_double_count_metrics_or_emit_spans(monkeypatch):
    """The replay recreates state that the lost container already reported."""
    counts = []

    class Counter:
        def add(self, *a, **k):
            counts.append("add")

        def record(self, *a, **k):
            counts.append("record")

    for name in ("_guardrail", "_offers", "_fallbacks", "_needs_human", "_notify_latency"):
        monkeypatch.setattr(metrics, name, Counter())
    spans = []

    class Tracer:
        def start_as_current_span(self, name, **k):
            import contextlib

            spans.append(name)
            return contextlib.nullcontext(telemetry.trace.INVALID_SPAN)

    monkeypatch.setattr(telemetry, "tracer", Tracer())

    a, b = (build_customer_engine_app() for _ in range(2))
    a.set_up()
    b.set_up()
    a.scenario_start("happy-path")
    for step in range(4):
        await a.scenario_step(preset="happy-path", step=step)
    jid = a.scenario_state()["journey_id"]
    offer = dict(
        offer_id="11111111-1111-4111-8111-111111111111", journey_id=jid, flight="AC1252",
        cabin="premium_business", price="135.00", standard_price="290.00", net_margin="80.00",
        currency="USD", origin="sales", optional=True,
    )  # fmt: skip
    assert (await a.a2a_skill("propose_offer", {"offer": offer}))["status"] == "approved"
    await a.scenario_step(preset="happy-path", step=4)  # accept
    assert counts, "the real run does emit metrics"
    counts.clear()
    spans.clear()
    b.scenario_state(preset="happy-path", step=5)  # replays the 5 steps (a2a offer, accept ...)
    assert counts == []  # no metrics from the replay
    assert not [s for s in spans if s != "acme.engine.op"]  # no replayed spans either


async def test_replay_does_not_call_the_configured_model():
    """Replay must be deterministic and free: it never uses the deployed (Vertex) LLM."""
    a = build_customer_engine_app()
    a.set_up()

    class Boom:
        async def compose_passenger_message(self, ctx):
            raise AssertionError("replay called the model")

        async def parse_passenger_request(self, text):
            raise AssertionError("replay called the model")

    a.runtime.llm = Boom()
    state = a.scenario_state(preset="happy-path", step=4)  # replays cancel, notify, 2 replies
    assert state["step_index"] == 4
    assert isinstance(a.runtime.llm, Boom)  # restored afterwards


def through_agent_engine(**kwargs):
    """Arguments as the platform delivers them: JSON numbers become doubles (protobuf Struct)."""
    from google.protobuf import json_format, struct_pb2

    s = struct_pb2.Struct()
    s.update(kwargs)
    return json_format.MessageToDict(s)


def test_the_struct_round_trip_really_turns_ints_into_floats():
    got = through_agent_engine(preset="happy-path", step=5)
    assert got["step"] == 5.0 and isinstance(got["step"], float)


async def test_a_rebuild_works_with_arguments_that_went_through_a_protobuf_struct():
    """#279: live, the step arrived as 5.0 and the recovery path failed with `bad scenario step`."""
    a, b = (build_customer_engine_app() for _ in range(2))
    a.set_up()
    b.set_up()
    a.scenario_start("happy-path")
    for step in range(3):
        await a.scenario_step(**through_agent_engine(preset="happy-path", step=step))
    want = a.scenario_state(**through_agent_engine(preset="happy-path", step=3))
    got = b.scenario_state(**through_agent_engine(preset="happy-path", step=3))  # b rebuilds
    assert got["step_index"] == 3 and isinstance(got["step_index"], int)
    assert {k: got[k] for k in STRUCTURAL} == {k: want[k] for k in STRUCTURAL}
    events = b.journey_events(
        got["journey_id"], **through_agent_engine(since=1, preset="happy-path", step=3)
    )
    assert len(events) == len(b.journey_events(got["journey_id"])) - 1  # `since` 1.0 sliced


@pytest.mark.parametrize("step", [5.0, 5])
def test_integral_values_are_accepted_as_ints(step):
    from acme.engines.customer import integral

    assert integral(step, "x") == 5 and isinstance(integral(step, "x"), int)


@pytest.mark.parametrize("bad", [5.5, True, "5", None, float("nan"), float("inf"), 2.0**60])
def test_non_whole_numbers_are_rejected(bad):
    from acme.engines.customer import EngineOperationError, integral

    with pytest.raises(EngineOperationError, match="bad x"):
        integral(bad, "x")


@pytest.mark.parametrize("step", [5.5, True, "5", -1, -1.0, 99.0])
def test_a_bad_step_is_rejected_by_the_engine(step):
    from acme.engines.customer import EngineOperationError

    a = build_customer_engine_app()
    a.set_up()
    with pytest.raises(EngineOperationError, match="bad scenario step"):
        a.scenario_state(preset="happy-path", step=step)
