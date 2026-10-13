"""#215: spans from the deployed engines must reach the exporter, not sit in a process buffer.

The live proof found no `acme.a2a.*` spans and no `acme.scenario` on some Customer step traces.
Agent Engine runs several processes per container and can throttle a process between requests,
so a BatchSpanProcessor's timer may not run for a long time after the last request: spans of the
final operation of a burst stay in the buffer. Fix under test: every engine operation flushes the
tracer provider before returning, and each scenario turn runs inside a span the code owns.
These tests use a batch processor whose timer never fires, and the real engine entrypoints.
"""

from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import INVALID_SPAN, SpanKind

from acme import telemetry
from acme.engines.customer import build_customer_engine_app
from acme.engines.sales import build_sales_engine_app
from tests.governance.flow import Flow, engine_over

PID = "P-MARIANA"


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("ACME_STATE_DB", raising=False)
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    # The timer never fires within a test: only an explicit flush can export (like a throttled
    # process between requests).
    provider.add_span_processor(BatchSpanProcessor(exp, schedule_delay_millis=3_600_000))
    monkeypatch.setattr(telemetry, "tracer", provider.get_tracer("acme"))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    yield exp
    provider.shutdown()


def spans(exp, name=None):
    done = exp.get_finished_spans()
    return [s for s in done if name is None or s.name == name]


def attrs(s):
    return dict(s.attributes or {})


def worker():
    app = build_customer_engine_app()
    app.set_up()
    return app


def offer(jid):
    return dict(
        offer_id=str(uuid4()), journey_id=jid, flight="AC1252", cabin="premium_business",
        price="135.00", standard_price="290.00", net_margin="80.00", currency="USD",
        origin="sales", optional=True,
    )  # fmt: skip


async def test_scenario_step_exports_its_spans_before_returning(exporter):
    app = worker()
    app.scenario_start("happy-path")
    exporter.clear()
    await app.scenario_step()
    (step,) = spans(exporter, "acme.scenario.step")  # exported without waiting for any timer
    assert attrs(step)["acme.scenario"] == "happy-path"


@pytest.mark.parametrize(
    "scenario", ["happy-path", "declines-upgrade", "unsatisfiable-request", "seat-taken"]
)
async def test_every_turn_has_a_span_the_code_owns_with_the_scenario_attribute(exporter, scenario):
    """Turn 1 (cold session) and later turns (replans, accept/decline) alike."""
    app = worker()
    app.scenario_start(scenario)
    state = app.scenario_state()
    turn = 0
    while not state["done"]:
        if (
            state["next_step"]
            and "Accept" in state["next_step"]
            or (state["next_step"] and "No thanks" in state["next_step"])
        ):
            await app.a2a_skill("propose_offer", {"offer": offer(state["journey_id"])})
        exporter.clear()
        state = await app.scenario_step()
        turn += 1
        owned = spans(exporter, "acme.scenario.step")
        assert len(owned) == 1, f"turn {turn}: {[s.name for s in spans(exporter)]}"
        assert attrs(owned[0])["acme.scenario"] == scenario
        assert (
            attrs(owned[0])["acme.step"] == str(turn - 1)
            or attrs(owned[0])["acme.step"] == turn - 1
        )
    assert turn >= 2


async def test_scenario_attribute_does_not_depend_on_the_current_span_recording(
    exporter, monkeypatch
):
    import acme.engines.customer as mod

    monkeypatch.setattr(mod.trace, "get_current_span", lambda *a, **k: INVALID_SPAN)
    app = worker()
    app.scenario_start("happy-path")
    exporter.clear()
    await app.scenario_step()
    (step,) = spans(exporter, "acme.scenario.step")
    assert attrs(step)["acme.scenario"] == "happy-path"


async def test_a2a_skill_exports_acme_a2a_spans_before_returning(exporter):
    app = worker()
    app.scenario_start("happy-path")
    for _ in range(4):
        await app.scenario_step()
    exporter.clear()
    out = await app.a2a_skill("propose_offer", {"offer": offer(app.scenario_state()["journey_id"])})
    assert out["status"] == "approved"
    (a2a,) = spans(exporter, "acme.a2a.propose_offer")
    assert a2a.kind == SpanKind.SERVER and attrs(a2a)["acme.skill"] == "propose_offer"


async def test_sales_engine_exports_pitch_and_a_linked_a2a_client_span(exporter):
    f = Flow("engine")
    await f.cancel()
    await f.notify()
    await f.rt.handle_inbound(PID, "Can I leave after 10am?")
    await f.rt.handle_inbound(PID, "Perfect, thanks!")  # acknowledged, no offer yet
    exporter.clear()
    app = build_sales_engine_app(engine_over(f.rt))
    app.set_up()
    events = [e async for e in app.async_stream_query(message="tick", user_id="u")]
    assert events[-1]["content"]["parts"][0]["text"] == "proposed 1 offer(s)"
    (pitch,) = spans(exporter, "acme.sales.pitch")  # exported when the operation returns
    clients = [s for s in spans(exporter, "acme.a2a.propose_offer") if s.kind == SpanKind.CLIENT]
    (client,) = clients
    assert client.context.trace_id == pitch.context.trace_id  # same trace as the pitch
    assert client.parent is not None and client.parent.span_id == pitch.context.span_id
    assert attrs(client)["acme.skill"] == "propose_offer"
    await f.http.aclose()


def test_flush_is_a_noop_without_an_sdk_provider(monkeypatch):
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: object())
    telemetry.flush()  # must not raise
    monkeypatch.setattr(
        trace, "get_tracer_provider", lambda: type("P", (), {"force_flush": lambda s, t: 1 / 0})()
    )
    telemetry.flush()  # a failing flush never breaks an operation
