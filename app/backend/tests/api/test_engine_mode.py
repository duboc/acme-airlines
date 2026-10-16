"""`ENGINE_MODE=agent-engine` (#164): the HTTP API served from engines (routed to local apps)."""

import inspect
import logging
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from acme.api.app import create_app
from acme.api.engine_backend import EngineBackend
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


PARITY_FIELDS = (*STRUCTURAL, "seat_reason")


async def run(c, sid):
    state = (await c.post(f"/scenarios/{sid}/start")).json()
    steps = [{k: state[k] for k in PARITY_FIELDS}]
    while not state["done"]:
        r = await c.post(f"/scenarios/{sid}/step")
        assert r.status_code == 200, r.text
        state = r.json()
        steps.append({k: state[k] for k in PARITY_FIELDS})
    phone = (await c.get("/whatsapp/P-MARIANA/messages")).json()
    shape = [(m["direction"], bool(m["buttons"]), bool(m["attachments"])) for m in phone]
    return state, steps, (await c.get("/journeys")).json(), shape


@pytest.mark.parametrize("sid", IDS)
async def test_presets_through_the_http_api_match_local_mode(sid, engine_app):
    async with client(create_app()) as local, client(engine_app) as remote:
        want, want_steps, want_journeys, want_phone = await run(local, sid)
        got, got_steps, got_journeys, got_phone = await run(remote, sid)
        assert got_steps == want_steps  # step-by-step 11-field STRUCTURAL + seat_reason parity
        assert {k: got[k] for k in PARITY_FIELDS} == {k: want[k] for k in PARITY_FIELDS}
        if sid == "seat-taken":
            assert got["seat"] == "15A" and got["seat_reason"] and "15A" in got["seat_reason"]
        assert got_phone == want_phone  # the phone panel, structurally
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


async def test_phone_reset_and_trigger_routes_match_local(engine_app):
    async with client(create_app()) as local, client(engine_app) as remote:
        for c in (local, remote):
            await c.post("/scenario/reset")
            assert (
                await c.post(
                    "/scenario/trigger-cancellation", json={"flight": "AC9", "reason": "x"}
                )
            ).status_code == 404
            assert (await c.get("/whatsapp/P-NOPE/messages")).status_code == 404
            assert (
                await c.post("/whatsapp/P-MARIANA/actions", json={"action": "accept_offer"})
            ).status_code == 409
            assert (
                await c.post("/whatsapp/P-MARIANA/messages", json={"text": ""})
            ).status_code == 422
        results = []
        for c in (local, remote):
            r = await c.post(
                "/scenario/trigger-cancellation", json={"flight": "AC1234", "reason": "x"}
            )
            await c.post("/whatsapp/P-MARIANA/messages", json={"text": "Perfect, thanks!"})
            msgs = (await c.get("/whatsapp/P-MARIANA/messages")).json()
            results.append((r.status_code, r.json()["journey_ids"], [m["direction"] for m in msgs]))
        assert results[0] == results[1]
        await remote.post("/scenario/reset")
        assert (await remote.get("/journeys")).json() == []


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


GOOD = "projects/acme-airlines-factory/locations/us-central1/reasoningEngines/1"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"AGENT_ENGINE_CUSTOMER": GOOD},
        {"AGENT_ENGINE_CUSTOMER": "x", "AGENT_ENGINE_SALES": "y"},
    ],
)
async def test_bad_engine_config_starts_and_fails_at_request_time(monkeypatch, env):
    """#199: nothing resolves at startup; a bad config is a clear 503 on the first engine call."""
    monkeypatch.setenv("ENGINE_MODE", "agent-engine")
    for k in ("AGENT_ENGINE_CUSTOMER", "AGENT_ENGINE_SALES"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    async with client(create_app()) as c:
        assert (await c.get("/healthz")).json()["engine"] == "agent-engine"
        r = await c.get("/scenarios")
    assert r.status_code == 503
    assert "AGENT_ENGINE_" in r.json()["detail"]


async def test_engines_are_resolved_lazily_without_adc(monkeypatch):
    """#199: valid names, no ADC. Startup must not touch Vertex; the first call fails clearly."""
    import sys

    monkeypatch.setenv("ENGINE_MODE", "agent-engine")
    monkeypatch.setenv("AGENT_ENGINE_CUSTOMER", GOOD)
    monkeypatch.setenv("AGENT_ENGINE_SALES", GOOD)
    monkeypatch.delitem(sys.modules, "vertexai", raising=False)
    app = create_app()
    assert "vertexai" not in sys.modules

    import acme.api.engine_backend as eb

    calls = []

    def no_adc(self, name):
        calls.append(name)
        raise RuntimeError("DefaultCredentialsError: no ADC")

    monkeypatch.setattr(eb._AdcEngine, "_connect", no_adc)
    async with client(app) as c:
        r = await c.get("/scenarios")
        assert r.status_code == 503 and "unavailable" in r.json()["detail"]
        assert "ADC" not in r.json()["detail"]
        await c.get("/scenarios")
    assert len(calls) == 2  # failures are not cached: a later request retries


def test_valid_names_are_accepted_by_project_id_or_number(monkeypatch):
    from acme.api.engine_backend import _resource

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "acme-airlines-factory")
    env = {"X": GOOD, "Y": GOOD.replace("acme-airlines-factory", "123456789")}
    assert _resource(env, "X") == GOOD and _resource(env, "Y")


def test_supervisor_starts_no_local_sim_or_sales_in_agent_engine_mode():
    assert child_names({}) == ("sim", "customer")
    assert child_names({"ENGINE_MODE": "local"}) == ("sim", "customer")
    assert child_names({"ENGINE_MODE": "agent-engine"}) == ("customer",)


BOTH = "PORT\nAGENT_ENGINE_CUSTOMER\nAGENT_ENGINE_SALES"


@pytest.mark.parametrize(
    ("policy", "names", "mode"),
    [
        ("local", BOTH, "local"),  # `local` never switches
        ("auto", BOTH, "agent-engine"),
        ("auto", "AGENT_ENGINE_CUSTOMER", "local"),
        ("auto", "", "local"),
        ("", BOTH, "local"),
    ],
)
def test_deploy_mode_selection(policy, names, mode):
    run_ = subprocess.run(
        ["sh", str(SCRIPT), policy, *names.split()], capture_output=True, text=True
    )
    assert run_.stdout.strip() == mode


def test_workflow_ships_hosted_engine_mode_auto():
    wf = (SCRIPT.parents[2] / ".github/workflows/irrops-deploy.yml").read_text()
    assert "\n  HOSTED_ENGINE_MODE: auto\n" in wf


async def test_backend_logs_no_passenger_data(engine_app, caplog):
    caplog.set_level(logging.DEBUG)
    async with client(engine_app) as c:
        await run(c, "happy-path")
    text = caplog.text
    assert "Mariana" not in text and "MSLV42" not in text


async def test_manual_whatsapp_reply_ticks_sales_and_refreshes_state_in_local_and_engine_modes(
    engine_app,
):
    """#322 AC1 & AC2: manual WhatsApp reply after step 3 ticks Sales to propose the $135 AC1252
    offer without calling /scenarios/happy-path/step, and refreshes _step and _tape."""
    backend = engine_app.state.engine_backend
    async with client(create_app()) as local, client(engine_app) as remote:
        for c in (local, remote):
            await c.post("/scenarios/happy-path/start")
            for _ in range(3):  # cancel, notify, "Can I leave after 10am?"
                assert (await c.post("/scenarios/happy-path/step")).status_code == 200
            r = await c.post("/whatsapp/P-MARIANA/messages", json={"text": "Perfect, thanks!"})
            assert r.status_code == 201 and "tape" not in r.json()
            (jid,) = [j["journey_id"] for j in (await c.get("/journeys")).json()]
            detail = (await c.get(f"/journeys/{jid}")).json()
            assert detail["resolution_acknowledged"] is True
            assert detail["offer"] is not None and detail["offer"]["status"] == "sent"
            assert detail["offer"]["flight"] == "AC1252" and detail["offer"]["price"] == "135.00"
            act = await c.post("/whatsapp/P-MARIANA/actions", json={"action": "accept_offer"})
            assert act.status_code == 200 and act.json()["offer_status"] == "accepted"
            assert "tape" not in act.json()
            after = (await c.get(f"/journeys/{jid}")).json()
            assert after["status"] == "CLOSED" and after["seat"]["seat"] == "2A"
            assert after["offer"]["status"] == "accepted"
    assert backend._step == 5 and backend.done  # noqa: SLF001
    assert any(e.get("m") == "parse" for e in (backend._tape or []))  # noqa: SLF001
    assert any(e.get("k") == "offer" for e in (backend._tape or []))  # noqa: SLF001


@pytest.mark.parametrize("sid", ["happy-path", "declines-upgrade"])
async def test_scripted_accept_or_decline_after_blocked_offer_returns_409_in_engine_mode(
    engine_app, metric_points, sid
):
    """#455 AC2: in agent-engine mode, when a manual unhandled WhatsApp question opens needs_human
    and blocks the offer, step 5 (accept/decline) returns 409 no_offer_sent (not 503), keeps
    backend._step == 4 and backend._tape intact, and leaks no 'tape' key in HTTP responses."""
    backend = engine_app.state.engine_backend
    before = list(metric_points().get("acme.http.server.errors", []))
    async with client(engine_app) as c:
        await c.post(f"/scenarios/{sid}/start")
        for _ in range(2):
            assert (await c.post(f"/scenarios/{sid}/step")).status_code == 200
        m = await c.post(
            "/whatsapp/P-MARIANA/messages", json={"text": "Where do I claim my pet crate?"}
        )
        assert m.status_code == 201 and "tape" not in m.json()
        for _ in range(2):
            s = await c.post(f"/scenarios/{sid}/step")
            assert s.status_code == 200 and "tape" not in s.json()
        assert s.json()["step_index"] == 4 and s.json()["offers_sent"] == 0

        r = await c.post(f"/scenarios/{sid}/step")
        assert r.status_code == 409
        assert r.json() == {
            "code": "no_offer_sent",
            "detail": "no offer has been sent to this passenger",
        }
        assert "tape" not in r.json()
        assert backend._step == 4 and not backend.done  # noqa: SLF001
        assert isinstance(backend._tape, list) and len(backend._tape) >= 3  # noqa: SLF001
        assert list(metric_points().get("acme.http.server.errors", [])) == before
