"""#279, live run 6: the deployed model answered Other for "Can I leave after 10am?", so no offer
was sent and the scripted decline died with NoSentOffer. The model stays primary; only an Other
answer falls back to the deterministic time pattern."""

import pytest

from acme.api.engine_backend import EngineBackend
from acme.llm.fake import FakeLlm
from acme.llm.port import Acknowledge, DepartureAfter, Other
from tests.engines.test_replay_tape import build, plain_run
from tests.live.agent_engine_proof import EngineCallFailed, engine_call


@pytest.fixture(autouse=True)
def fake_mode(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.delenv("ACME_STATE_DB", raising=False)


class OtherForEverything(FakeLlm):
    async def parse_passenger_request(self, text):
        return Other(text=text)


class OtherForAfter(FakeLlm):
    async def parse_passenger_request(self, text):
        if "after" in text:
            return Other(text=text)  # what the live model did in run 6
        return await super().parse_passenger_request(text)


class Broken(FakeLlm):
    async def parse_passenger_request(self, text):
        if "after" in text:
            raise TimeoutError
        return await super().parse_passenger_request(text)


class RaisesForEverything(FakeLlm):
    async def parse_passenger_request(self, text):
        raise TimeoutError


class AlwaysAck(FakeLlm):
    async def parse_passenger_request(self, text):
        return Acknowledge()


@pytest.mark.parametrize("llm", [OtherForAfter(), Broken()])
async def test_a_model_that_says_other_does_not_lose_the_after_time_request(llm):
    app, cust, sales = build(llm)
    backend = EngineBackend(cust, sales)
    state = await plain_run(backend, "declines-upgrade")  # used to die with NoSentOffer
    assert state["done"] and state["offers_sent"] == 1


async def test_the_pattern_never_overrides_a_non_other_model_answer():
    app, cust, _ = build(AlwaysAck())
    from acme.customer import outreach

    assert isinstance(await outreach.classify(app.runtime, "Perfect, thanks!"), Acknowledge)
    assert app.runtime.last_parse == {"parse_kind": "ack", "parse_source": "model"}


async def test_the_fallback_is_recorded_without_text_in_the_op_diag_context():
    app, _, _ = build(OtherForEverything())
    from acme.customer import outreach

    got = await outreach.classify(app.runtime, "Can I leave after 10am?")
    assert isinstance(got, DepartureAfter)
    ctx = app._op_context()  # noqa: SLF001
    assert ctx["parse_kind"] == "departure_after" and ctx["parse_source"] == "pattern"
    assert "10" not in repr({k: v for k, v in ctx.items() if k.startswith("parse")})
    assert "parse_kind" not in app._op_context()  # noqa: SLF001 - reported once, then cleared
    await outreach.classify(app.runtime, "I would like a refund")
    assert app._op_context()["parse_source"] == "model"  # noqa: SLF001


async def test_a_stale_state_never_moves_the_step_counter_backwards():
    app, cust, sales = build()
    backend = EngineBackend(cust, sales)
    await backend.scenario_start("declines-upgrade")
    await backend.scenario_step("declines-upgrade")
    assert backend._step == 1  # noqa: SLF001
    real = cust.call

    async def stale(op, **kw):  # another worker has not seen the write yet
        state = await real(op, **kw)
        return {**state, "step_index": 0} if op == "scenario_state" else state

    cust.call = stale
    await backend.scenario_state()
    assert backend._step == 1  # noqa: SLF001
    await backend.scenario_start("declines-upgrade")  # a new scenario resets it
    assert backend._step == 0  # noqa: SLF001


async def test_an_engine_call_failure_keeps_only_the_class_and_our_context():
    async def boom():
        raise RuntimeError("Request Data: {'tape': [{'text': 'Hi Mariana'}]}")

    with pytest.raises(EngineCallFailed) as err:
        await engine_call(boom(), "scenario_step", "declines-upgrade", 4)
    text = str(err.value) + repr(err.value.__cause__)
    assert "RuntimeError" in text and "scenario_step" in text and "step 4" in text
    assert "Mariana" not in text and "tape" not in text
    assert err.value.__suppress_context__


@pytest.mark.parametrize(
    ("llm", "text", "kind", "source"),
    [
        (
            RaisesForEverything(),
            "I would like a refund",
            "other",
            "model",
        ),  # fail closed: Other, same text
        (RaisesForEverything(), "Perfect, thanks!", "other", "model"),  # fail closed on ACK too
        (OtherForEverything(), "Perfect, thanks!", "other", "model"),
        (OtherForAfter(), "I would like a refund", "other", "model"),
        (FakeLlm(), "Perfect, thanks!", "ack", "model"),
        (FakeLlm(), "Can I leave after 10am?", "departure_after", "model"),
    ],
)
async def test_given_a_passenger_text_when_classified_then_the_result_and_the_diag_name_it(
    llm, text, kind, source
):
    from acme.customer import outreach

    app, _, _ = build(llm)
    got = await outreach.classify(app.runtime, text)
    if kind == "other":
        assert got == Other(text=text)  # the passenger's own words go to a person
    assert app.runtime.last_parse == {"parse_kind": kind, "parse_source": source}


@pytest.mark.parametrize("sid", [
    "happy-path", "declines-upgrade", "unsatisfiable-request", "seat-taken",
    "hotel-unavailable", "upgrade-race",
])  # fmt: skip
@pytest.mark.parametrize("llm", [OtherForAfter(), Broken()])
async def test_all_six_presets_match_fake_llm_parity_on_after_time_fallback(sid, llm):
    from tests.api.test_engine_mode import PARITY_FIELDS

    _, ref_cust, ref_sales = build(FakeLlm())
    want = await plain_run(EngineBackend(ref_cust, ref_sales), sid)
    _, got_cust, got_sales = build(llm)
    got = await plain_run(EngineBackend(got_cust, got_sales), sid)
    assert got["done"] is True
    assert {k: got[k] for k in PARITY_FIELDS} == {k: want[k] for k in PARITY_FIELDS}
