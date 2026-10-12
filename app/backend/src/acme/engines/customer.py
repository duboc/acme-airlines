"""Customer agent + specialists packaged as the Agent Engine app `acme-customer-agent` (#159).

A custom Agent Engine template (``set_up`` + ``register_operations``) over the unchanged
`CustomerRuntime`, `CustomerSkills` and ADK `CustomerAgent`: behaviour is not forked. The mocked
enterprise systems run in-process (`inprocess_connector`), so the engine makes no network calls
of its own. Operations: ``async_stream_query`` / ``stream_query`` (ADK turn) and ``a2a_skill``
(the same three-skill dispatch as the HTTP ``/a2a`` path, enforced server-side).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

from acme.llm.config import LlmConfigError, load_llm_mode, load_vertex_config

DISPLAY_NAME = "acme-customer-agent"
APP_NAME = "acme-customer"
ENGINE_REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent-engines]>=1.112,<2",
    "google-adk[a2a]>=1.10.0",
    "google-genai>=2.29.0",
    "mcp>=1.9,<2",
    "httpx>=0.28.1",
    "pydantic>=2.14.0",
    "fastapi>=0.143.0",
]


class EngineConfigError(ValueError):
    """The engine environment is invalid (e.g. a non-global Vertex location)."""


def engine_requirements() -> list[str]:
    """`requirements=` for `agent_engines.create/update` (the deploy issue uses this)."""
    return list(ENGINE_REQUIREMENTS)


def engine_extra_packages() -> list[str]:
    """`extra_packages=` for `agent_engines.create/update`, relative to `app/backend`."""
    return ["src/acme"]


def validate_env(env: Mapping[str, str] | None = None) -> str:
    """Fail fast on a bad environment; returns the LLM mode. Vertex means global, ADC, no keys."""
    env = os.environ if env is None else env
    try:
        mode = load_llm_mode(env)
        if mode == "vertex":
            load_vertex_config(env)
    except LlmConfigError as exc:
        raise EngineConfigError(str(exc)) from exc
    return mode


class CustomerEngineApp:
    def __init__(self) -> None:
        self.llm_mode = validate_env()

    def set_up(self) -> None:
        """Runs once inside the engine (and in tests): build the runtime, agent and runner."""
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from acme.customer.adk_agent import build_customer_agent
        from acme.customer.runtime import CustomerRuntime
        from acme.customer.skills import CustomerSkills

        self.runtime = CustomerRuntime()  # in-process simulators, LlmPort from LLM_MODE
        self.skills = CustomerSkills(self.runtime)
        self._sessions = InMemorySessionService()
        self._runner = Runner(
            app_name=APP_NAME, agent=build_customer_agent(self.runtime),
            session_service=self._sessions,
        )  # fmt: skip

    async def async_stream_query(
        self, *, message: str, user_id: str, session_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        from google.genai import types

        sid = session_id or f"s-{user_id}"
        if (
            await self._sessions.get_session(app_name=APP_NAME, user_id=user_id, session_id=sid)
            is None
        ):
            await self._sessions.create_session(app_name=APP_NAME, user_id=user_id, session_id=sid)
        content = types.Content(role="user", parts=[types.Part(text=message)])
        async for event in self._runner.run_async(
            user_id=user_id, session_id=sid, new_message=content
        ):
            yield event.model_dump(mode="json", exclude_none=True)

    def stream_query(
        self, *, message: str, user_id: str, session_id: str | None = None
    ) -> Iterator[dict[str, Any]]:
        import asyncio

        async def collect() -> list[dict[str, Any]]:
            return [
                e async for e in self.async_stream_query(
                    message=message, user_id=user_id, session_id=session_id
                )
            ]  # fmt: skip

        yield from asyncio.run(collect())

    async def a2a_skill(self, skill: str, args: dict[str, Any]) -> dict[str, Any]:
        """Same dispatch as HTTP `/a2a`: returns the skill's data, or `{"error": {...}}`."""
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {"message": {
                "messageId": "engine", "role": "agent",
                "parts": [{"kind": "data", "data": {"skill": skill, "args": args}}],
            }},
        }  # fmt: skip
        reply = await self.skills.handle_a2a(request)
        if "error" in reply:
            return {"error": reply["error"]}
        return reply["result"]["parts"][0]["data"]

    def register_operations(self) -> dict[str, list[str]]:
        return {
            "stream": ["stream_query"],
            "async_stream": ["async_stream_query"],
            "async": ["a2a_skill"],
        }


def build_customer_engine_app() -> CustomerEngineApp:
    return CustomerEngineApp()
