"""`ENGINE_MODE=agent-engine` (#164): the HTTP API served from engines (routed to local apps)."""

import inspect
import logging
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from acme.api.app import create_app
from acme.api.engine_backend import EngineBackend, EngineConfigError
from acme.demo.supervisor import child_names
from acme.engines.customer import build_customer_engine_app
from acme.engines.sales import build_sales_engine_app

IDS = ["happy-path", "declines-upgrade", "unsatisfiable-request", "seat-taken", "hotel-unavailable",
       "upgrade-race"]  # fmt: skip
STRUCTURAL = ("scenario_id", "step_index", "total_steps", "done", "journey_status", "flight",
              "seat", "offers_sent", "offer_status", "charged", "needs_human")  # fmt: skip
SCRIPT = Path(__file__).resolve().parents[4] / "app/demo/select-engine-mode.sh"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(self, address, *a, **k):
        raise AssertionError(f"network call blocked: {address}")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("ENGINE_MODE", raising=False)


class LocalEngine:
    """Routes `EngineHandle` calls to a local engine app (what Vertex does remotely)."""

    def __init__(self, app):
        self.app = app

    async def call(self, op, **kwargs):
        out = getattr(self.app, op)(**kwargs)
        return await out if inspect.isawaitable(out) else out

    def stream(self, **kwargs):
        return self.app.async_stream_query(**kwargs)


def engine_backend():
    customer = build_customer_engine_app()
    customer.set_up()
    sales = build_sales_engine_app(customer_engine=customer)
    sales.set_up()
    return EngineBackend(LocalEngine(customer), LocalEngine(sales))


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


@pytest.fixture
def engine_app(monkeypatch):
    from acme.api.engine_backend import create_engine_app

    return create_engine_app(engine_backend())


async def run(c, sid):
    state = (await c.post(f"/scenarios/{sid}/start")).json()
    while not state["done"]:
        r = await c.post(f"/scenarios/{sid}/step")
        assert r.status_code == 200, r.text
        state = r.json()
    return state, (await c.get("/journeys")).json()


@pytest.mark.parametrize("sid", IDS)
async def test_presets_through_the_http_api_match_local_mode(sid, engine_app):
    async with client(create_app()) as local, client(engine_app) as remote:
        want, want_journeys = await run(local, sid)
        got, got_journeys = await run(remote, sid)
        assert {k: got[k] for k in STRUCTURAL} == {k: want[k] for k in STRUCTURAL}
        assert got_journeys == want_journeys
        if want_journeys:
            jid = want_journeys[0]["journey_id"]
            d_local = await local.get(f"/journeys/{jid}")
            d_remote = await remote.get(f"/journeys/{jid}")
            for k in ("status", "resolution_acknowledged", "seat", "verification"):
                assert d_remote.json()[k] == d_local.json()[k]
            ev = await remote.get(f"/journeys/{jid}/events?limit=3")
            assert ev.text.count("data:") == 3


async def test_route_errors_match_local(engine_app):
    async with client(engine_app) as c:
        assert (await c.post("/scenarios/nope/start")).status_code == 404
        assert (await c.post("/scenarios/seat-taken/step")).status_code == 409  # not started
        assert (await c.get("/journeys/J-nope")).status_code == 404
        await run(c, "seat-taken")
        assert (await c.post("/scenarios/seat-taken/step")).status_code == 409  # finished
        assert (await c.get("/healthz")).json()["engine"] == "agent-engine"


async def test_a_step_runs_a_sales_engine_turn_after_the_customer_step(engine_app):
    calls = []
    backend = engine_app.state.engine_backend
    for name, h in (("customer", backend._customer), ("sales", backend._sales)):
        call, stream = h.call, h.stream
        h.call = lambda op, _c=call, _n=name, **kw: (calls.append((_n, op)), _c(op, **kw))[1]
        h.stream = lambda _s=stream, _n=name, **kw: (calls.append((_n, "turn")), _s(**kw))[1]
    async with client(engine_app) as c:
        await c.post("/scenarios/seat-taken/start")
        calls.clear()
        await c.post("/scenarios/seat-taken/step")
    order = [c for c in calls if c in {("customer", "scenario_step"), ("sales", "turn")}]
    assert order == [("customer", "scenario_step"), ("sales", "turn")]


GOOD = "projects/p/locations/l/reasoningEngines/1"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"AGENT_ENGINE_CUSTOMER": GOOD},
        {"AGENT_ENGINE_CUSTOMER": "x", "AGENT_ENGINE_SALES": "y"},
    ],
)
def test_agent_engine_mode_fails_fast_without_resources(monkeypatch, env):
    monkeypatch.setenv("ENGINE_MODE", "agent-engine")
    for k in ("AGENT_ENGINE_CUSTOMER", "AGENT_ENGINE_SALES"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(EngineConfigError, match="AGENT_ENGINE_"):
        create_app()


def test_supervisor_starts_no_local_sim_or_sales_in_agent_engine_mode():
    assert child_names({}) == ("sim", "customer")
    assert child_names({"ENGINE_MODE": "local"}) == ("sim", "customer")
    assert child_names({"ENGINE_MODE": "agent-engine"}) == ("customer",)


BOTH = "PORT\nAGENT_ENGINE_CUSTOMER\nAGENT_ENGINE_SALES"


@pytest.mark.parametrize(
    ("policy", "names", "mode"),
    [
        ("local", BOTH, "local"),  # the committed default: never switches
        ("auto", BOTH, "agent-engine"),
        ("auto", "AGENT_ENGINE_CUSTOMER", "local"),
        ("auto", "", "local"),
        ("", BOTH, "local"),
    ],
)
def test_deploy_mode_selection(policy, names, mode):
    run_ = subprocess.run(["sh", str(SCRIPT), policy, names], capture_output=True, text=True)
    assert run_.stdout.strip() == mode


def test_workflow_ships_hosted_engine_mode_local():
    wf = (SCRIPT.parents[2] / ".github/workflows/irrops-deploy.yml").read_text()
    assert "\n  HOSTED_ENGINE_MODE: local\n" in wf


async def test_backend_logs_no_passenger_data(engine_app, caplog):
    caplog.set_level(logging.DEBUG)
    async with client(engine_app) as c:
        await run(c, "happy-path")
    text = caplog.text
    assert "Mariana" not in text and "MSLV42" not in text
