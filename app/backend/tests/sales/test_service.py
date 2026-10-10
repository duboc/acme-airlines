import pytest
from fastapi.testclient import TestClient
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from acme.sales.agent import build_sales_agent
from acme.sales.main import create_app, main


class FakeRadar:
    async def tick(self):
        return []


def test_card_has_no_inbound_skills():
    card = TestClient(create_app(FakeRadar())).get("/.well-known/agent-card.json").json()
    assert card["name"] == "sales_agent" and card["skills"] == []


async def test_adk_agent_runs_a_tick_without_a_model():
    runner = Runner(
        app_name="acme",
        agent=build_sales_agent(FakeRadar()),
        session_service=InMemorySessionService(),
    )
    await runner.session_service.create_session(app_name="acme", user_id="u", session_id="s")
    msg = types.Content(role="user", parts=[types.Part(text="tick")])
    events = [e async for e in runner.run_async(user_id="u", session_id="s", new_message=msg)]
    assert events[-1].content.parts[0].text == "no offers proposed"


def test_main_refuses_to_start_without_sales_token(monkeypatch):
    monkeypatch.delenv("ACME_SALES_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        main()
