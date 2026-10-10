"""Customer runtime on ADK: scenario-1 tests re-run unchanged over the ADK `MCPToolset`
connector, the deterministic `BaseAgent`, the A2A card scaffold, and the no-network guard."""

import os
import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from acme.api.app import create_app
from acme.customer.adk_agent import build_customer_agent
from acme.customer.runtime import CustomerRuntime
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.sim.adk_connector import adk_connector
from acme.sim.app import create_app as create_sim_app
from acme.sim.auth import load_tokens

# Scenario-1 tests, unchanged: they only use the `rt` fixture, redefined below.
from tests.customer.test_agent import *  # noqa: F401,F403,E402


@pytest.fixture(autouse=True)
def local_network_only(monkeypatch):
    """Network guard: only loopback connections (the served sim) are allowed."""
    real = socket.socket.connect

    def guarded(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"network call blocked: {address}")
        return real(self, address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    for var in ("LLM_MODE", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def adk_runtime():
    servers: list[uvicorn.Server] = []

    def factory(sim):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        tokens = load_tokens({})  # random; the ADK connector presents only the customer token
        app = create_sim_app(sim, tokens=tokens)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True).start()
        while not server.started:
            time.sleep(0.01)
        servers.append(server)
        return adk_connector(f"http://127.0.0.1:{sock.getsockname()[1]}", "customer", tokens)

    yield lambda: CustomerRuntime(
        FixedClock(DISRUPTION_DETECTED_AT), connector_factory=factory, auto_duty_of_care=False
    )
    for server in servers:
        server.should_exit = True


@pytest.fixture
def rt(adk_runtime):  # overrides the plain-connector fixture for the imported tests
    return adk_runtime()


async def test_base_agent_ingests_cancellation_via_runner(adk_runtime):
    rt = adk_runtime()
    await rt.client.call("occ", "flight_ops.publish_cancellation", flight="AC1234", reason="x")
    agent = build_customer_agent(rt)
    svc = InMemorySessionService()
    runner = Runner(app_name="acme", agent=agent, session_service=svc)
    await svc.create_session(app_name="acme", user_id="u", session_id="s")
    msg = types.Content(role="user", parts=[types.Part(text="poll")])
    events = [e async for e in runner.run_async(user_id="u", session_id="s", new_message=msg)]
    assert len(rt.journeys) == 1
    assert "opened journeys" in events[-1].content.parts[0].text
    assert "GOOGLE_GENAI_USE_VERTEXAI" not in os.environ  # no Vertex config in fake mode


def test_agent_card_served_with_sales_skills():
    client = TestClient(create_app())
    res = client.get("/.well-known/agent-card.json")
    assert res.status_code == 200
    card = res.json()
    assert card["name"] == "customer_agent"
    assert [x["id"] for x in card["skills"]] == [
        "list_journeys",
        "journey_status",
        "propose_offer",
    ]
    assert card["url"].startswith("http")
