"""Customer Agent Engine package (#159): local, fake LLM, no network."""

import socket
from uuid import uuid4

import pytest

from acme.customer.skills import CustomerSkills
from acme.domain.journey import DisruptionStatus
from acme.engines.customer import (
    DISPLAY_NAME,
    EngineConfigError,
    build_customer_engine_app,
    engine_extra_packages,
    engine_requirements,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(self, address, *a, **k):
        raise AssertionError(f"network call blocked: {address}")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.delenv("LLM_MODE", raising=False)


def a2a(skill, args, parts_extra=()):
    parts = [{"kind": "data", "data": {"skill": skill, "args": args}}, *parts_extra]
    return {
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {"messageId": "m1", "role": "agent", "parts": parts}},
    }  # fmt: skip


def offer(jid):
    return dict(
        offer_id=str(uuid4()), journey_id=jid, flight="AC1252", cabin="premium_business",
        price="135.00", standard_price="290.00", net_margin="80.00", currency="USD",
        origin="sales", optional=True,
    )  # fmt: skip


async def http_a2a(rt, skill, args):
    """The HTTP `/a2a` handler is `CustomerSkills(rt).handle_a2a` (see acme.api.app)."""
    return await CustomerSkills(rt).handle_a2a(a2a(skill, args))


def engine():
    app = build_customer_engine_app()
    app.set_up()
    app.runtime.auto_duty_of_care = False
    return app


async def test_stream_query_yields_agent_events():
    app = engine()
    rt = app.runtime
    await rt.client.call("occ", "flight_ops.publish_cancellation", flight="AC1234", reason="x")
    events = [e async for e in app.async_stream_query(message="poll", user_id="u", session_id="s")]
    assert events
    assert "opened journeys" in events[-1]["content"]["parts"][0]["text"]
    assert len(rt.journeys) == 1


async def test_a2a_skill_matches_http_path():
    app = engine()
    (jid,) = await app.runtime.trigger_cancellation("AC1234", "unscheduled maintenance")
    listed = await app.a2a_skill("list_journeys", {})
    expected = await http_a2a(app.runtime, "list_journeys", {})
    assert listed == expected["result"]["parts"][0]["data"] == {"journey_ids": [jid]}
    status = await app.a2a_skill("journey_status", {"journey_id": jid})
    http_status = await http_a2a(app.runtime, "journey_status", {"journey_id": jid})
    assert status == http_status["result"]["parts"][0]["data"]
    await app.runtime.sim.rebook("MSLV42", "AC1252")
    state = app.runtime.journeys[jid].state
    state.transition(DisruptionStatus.RESOLVED)
    state.resolution_acknowledged = True
    state.passenger_tier = "diamond"
    proposed = await app.a2a_skill("propose_offer", {"offer": offer(jid)})
    assert proposed["status"] == "approved"


@pytest.mark.parametrize(
    ("skill", "args"),
    [
        ("book_flight", {}),
        ("list_journeys", {"free_text": "hi"}),
        ("propose_offer", {"offer": {"pitch": "buy now"}}),
    ],
)
async def test_a2a_skill_rejects_bad_requests_with_http_error_and_no_state_change(skill, args):
    app = engine()
    await app.runtime.trigger_cancellation("AC1234", "unscheduled maintenance")
    before = sorted(app.runtime.journeys)
    http = await http_a2a(app.runtime, skill, args)
    got = await app.a2a_skill(skill, args)
    assert got["error"] == http["error"]  # identical code and message
    assert got["jsonrpc"] == "2.0"
    assert sorted(app.runtime.journeys) == before
    assert all(not r.state.offers for r in app.runtime.journeys.values())


def test_vertex_requires_global_model_location(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "vertex")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "acme-airlines-factory")
    monkeypatch.setenv("ACME_GENAI_LOCATION", "us-central1")
    with pytest.raises(EngineConfigError):
        build_customer_engine_app()


def test_injected_engine_region_does_not_change_the_model_location(monkeypatch):
    """On Agent Engine GOOGLE_CLOUD_LOCATION is the engine region (#190); it must be ignored."""
    monkeypatch.setenv("LLM_MODE", "vertex")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "acme-airlines-factory")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.delenv("ACME_GENAI_LOCATION", raising=False)
    assert build_customer_engine_app().llm_mode == "vertex"


def test_registered_operations_and_deploy_helpers():
    ops = build_customer_engine_app().register_operations()
    assert ops["async_stream"] == ["async_stream_query"]
    assert "a2a_skill" in ops["async"]
    assert DISPLAY_NAME == "acme-customer-agent"
    assert any(r.startswith("google-cloud-aiplatform") for r in engine_requirements())
    assert engine_extra_packages() == ["src/acme"]


@pytest.mark.parametrize(
    ("env", "text"),
    [
        ({"LLM_MODE": "bogus"}, "LLM_MODE"),
        ({"LLM_MODE": "vertex"}, "GOOGLE_CLOUD_PROJECT"),
    ],
)
def test_bad_env_fails_at_instantiation_with_a_clear_message(monkeypatch, env, text):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(EngineConfigError, match=text):
        build_customer_engine_app()


def test_vertex_with_project_and_global_location_builds(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "vertex")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "acme-airlines-factory")
    monkeypatch.setenv("ACME_GENAI_LOCATION", "global")
    assert build_customer_engine_app().llm_mode == "vertex"


async def test_stream_query_inside_a_running_loop_is_a_clear_error():
    app = engine()
    with pytest.raises(RuntimeError, match="async_stream_query"):
        next(app.stream_query(message="poll", user_id="u"))


def test_stream_query_works_outside_a_loop():
    app = engine()
    events = list(app.stream_query(message="poll", user_id="u"))
    assert events and events[-1]["author"] == "customer_agent"
