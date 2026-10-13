"""#266 part 2: scenario ops do not depend on container-local state.

Agent Engine can route consecutive calls to another replica or a restarted container (state in
container-local /tmp is lost). The caller passes `preset` and `step` on every scenario op; an engine
whose local state is not at that point rebuilds it deterministically from the seeded mocks.
Containers are simulated by separate engine apps with NO shared state, called round-robin."""

import inspect
import json
import logging

import httpx
import pytest

from acme import metrics, telemetry
from acme.api.app import create_app
from acme.api.engine_backend import EngineBackend, create_engine_app
from acme.engines.customer import build_customer_engine_app
from acme.engines.sales import build_sales_engine_app
from tests.api.test_engine_mode import IDS, STRUCTURAL

PID = "P-MARIANA"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("ACME_STATE_DB", raising=False)
    monkeypatch.delenv("ENGINE_MODE", raising=False)


class Containers:
    """N independent Customer engines (separate memory, no shared DB): each call goes to the next
    one, like requests routed across replicas / restarted containers."""

    def __init__(self, n=3):
        self.apps = []
        for _ in range(n):
            app = build_customer_engine_app()
            app.set_up()
            self.apps.append(app)
        self.i = 0
        self.pinned: int | None = None  # one Sales turn lands on one replica
        self.log: list[tuple[int, str]] = []

    def _next(self, op):
        if self.pinned is not None:
            idx = self.pinned
        else:
            idx = self.i % len(self.apps)
            self.i += 1
        self.log.append((idx, op))
        return self.apps[idx]

    async def call(self, op, **kwargs):
        out = getattr(self._next(op), op)(**kwargs)
        return await out if inspect.isawaitable(out) else out

    async def a2a_skill(self, skill, args):  # the Sales engine's handle on the Customer engine
        return await self._next("a2a_skill").a2a_skill(skill, args)


class Routed:
    def __init__(self, containers):
        self.c = containers

    async def call(self, op, **kwargs):
        return await self.c.call(op, **kwargs)

    def stream(self, **kwargs):
        return self.c._next("async_stream_query").async_stream_query(**kwargs)


class SalesRouted:
    def __init__(self, containers):
        self.containers = containers
        sales = build_sales_engine_app(customer_engine=containers)
        sales.set_up()
        self.sales = sales

    async def call(self, op, **kwargs):  # pragma: no cover - the backend only streams to Sales
        raise AssertionError(op)

    async def stream(self, **kwargs):
        c = self.containers
        c.pinned = c.i % len(c.apps)  # the Sales turn's calls all reach the same replica
        c.i += 1
        try:
            async for event in self.sales.async_stream_query(**kwargs):
                yield event
        finally:
            c.pinned = None


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def run(c, sid):
    state = (await c.post(f"/scenarios/{sid}/start")).json()
    while not state["done"]:
        r = await c.post(f"/scenarios/{sid}/step")
        assert r.status_code == 200, (sid, state["step_index"], r.text)
        state = r.json()
    journeys = (await c.get("/journeys")).json()
    phone = (await c.get(f"/whatsapp/{PID}/messages")).json()
    return state, journeys, [(m["direction"], bool(m["buttons"])) for m in phone]


@pytest.mark.parametrize("sid", IDS)
async def test_every_preset_completes_when_each_call_lands_on_another_container(sid):
    containers = Containers(3)
    backend = EngineBackend(Routed(containers), SalesRouted(containers))
    async with client(create_app()) as local, client(create_engine_app(backend)) as remote:
        want, want_journeys, want_phone = await run(local, sid)
        got, got_journeys, got_phone = await run(remote, sid)
    assert {k: got[k] for k in STRUCTURAL} == {k: want[k] for k in STRUCTURAL}
    assert got_journeys == want_journeys
    assert got_phone == want_phone  # the passenger sees the same conversation
    assert len({idx for idx, _ in containers.log}) == 3  # calls really were spread


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


class Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        msg = record.getMessage()
        if msg.startswith("ACME_DIAG "):
            self.records.append(json.loads(msg[len("ACME_DIAG ") :]))


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


def test_scenario_ops_declare_the_context_parameters():
    from acme.engines.customer import CustomerEngineApp

    ops = ("scenario_state", "scenario_step", "journeys_list", "journey_detail", "journey_events",
           "whatsapp_messages", "whatsapp_reply", "whatsapp_action")  # fmt: skip
    for op in ops:
        params = inspect.signature(getattr(CustomerEngineApp, op)).parameters
        assert "preset" in params and "step" in params, op
