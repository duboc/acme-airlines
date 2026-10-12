"""Customer agent + specialists packaged as the Agent Engine app `acme-customer-agent` (#159).

A custom Agent Engine template (``set_up`` + ``register_operations``) over the unchanged
`CustomerRuntime`, `CustomerSkills` and ADK `CustomerAgent`: behaviour is not forked. The mocked
enterprise systems run in-process (`inprocess_connector`), so the engine makes no network calls
of its own. Operations: ``async_stream_query`` / ``stream_query`` (ADK turn) and ``a2a_skill``
(the same three-skill dispatch as the HTTP ``/a2a`` path, enforced server-side).

Demo operations (#162): ``scenarios_list``, ``scenario_start``, ``scenario_step`` (the Customer
half: the demo drives the Sales tick), ``scenario_state``, ``journeys_list``, ``journey_detail``
and ``journey_events``. They reuse `acme.api.scenarios` and the API view models. Presets are
allowed with ``LLM_MODE=vertex`` here (the API app keeps its fake-only 404 rule); the world stays
deterministic, only the message wording comes from the LLM, and the fact check before sending is
unchanged (`acme.customer.outreach`).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, AsyncIterator, Iterator, Mapping
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types
from opentelemetry import trace
from pydantic import ConfigDict

from acme.llm.config import LlmConfigError, load_llm_mode, load_vertex_config

DISPLAY_NAME = "acme-customer-agent"
APP_NAME = "acme-customer"
ENGINE_REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent-engines]>=1.112,<2",
    "google-adk[a2a]>=1.10.0",
    "google-genai>=2.29.0",
    "mcp>=1.9,<2",
    "opentelemetry-sdk>=1.30",
    "opentelemetry-exporter-gcp-trace>=1.9",
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
            if not env.get("GOOGLE_CLOUD_PROJECT"):
                raise EngineConfigError("GOOGLE_CLOUD_PROJECT must be set when LLM_MODE=vertex")
            load_vertex_config(env)
    except LlmConfigError as exc:
        raise EngineConfigError(str(exc)) from exc
    return mode


class EngineOperationError(ValueError):
    """A demo operation was called in the wrong state (the API answers 404/409 for these)."""


class ScenarioStepAgent(BaseAgent):
    """One scripted scenario step, run as an ADK turn so it is traced like any other turn."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    scenarios: Any  # acme.api.scenarios.ScenarioRunner

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        runner = self.scenarios
        span = trace.get_current_span()
        span.set_attribute("acme.scenario", runner.active.id)  # ids only, never passenger data
        span.set_attribute("acme.step", runner.index)
        state = await runner.step(sales_tick=False)
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            content=types.Content(
                role="model",
                parts=[types.Part(text=f"step {state.step_index}/{state.total_steps}")],
            ),
        )


class CustomerEngineApp:
    def __init__(self) -> None:
        self.llm_mode = validate_env()

    def set_up(self) -> None:
        """Runs once inside the engine (and in tests): build the runtime, agent and runner."""
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from acme.api.scenarios import ScenarioRunner
        from acme.customer.adk_agent import build_customer_agent
        from acme.customer.runtime import CustomerRuntime
        from acme.customer.skills import CustomerSkills
        from acme.logging_setup import configure_logging
        from acme.telemetry import configure_cloud_trace

        configure_logging()  # no passenger PII in Cloud Logging (#170)
        configure_cloud_trace(DISPLAY_NAME)
        self.runtime = CustomerRuntime()  # in-process simulators, LlmPort from LLM_MODE
        self.skills = CustomerSkills(self.runtime)
        self._sessions = InMemorySessionService()
        self._runner = Runner(
            app_name=APP_NAME, agent=build_customer_agent(self.runtime),
            session_service=self._sessions,
        )  # fmt: skip
        self.scenarios = ScenarioRunner(self.runtime)
        self._step_runner = Runner(
            app_name=f"{APP_NAME}-scenario",
            agent=ScenarioStepAgent(name="scenario_step", scenarios=self.scenarios),
            session_service=self._sessions,
        )

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

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "stream_query cannot run inside a running event loop; use async_stream_query"
            )

        async def collect() -> list[dict[str, Any]]:
            return [
                e async for e in self.async_stream_query(
                    message=message, user_id=user_id, session_id=session_id
                )
            ]  # fmt: skip

        yield from asyncio.run(collect())

    async def a2a_skill(self, skill: str, args: dict[str, Any]) -> dict[str, Any]:
        """Same dispatch as HTTP `/a2a`: the skill's data, or its JSON-RPC error body as is."""
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {"message": {
                "messageId": "engine", "role": "agent",
                "parts": [{"kind": "data", "data": {"skill": skill, "args": args}}],
            }},
        }  # fmt: skip
        reply = await self.skills.handle_a2a(request)
        if "error" in reply:
            return reply
        return reply["result"]["parts"][0]["data"]

    # --- demo operations (#162): JSON-serialisable dicts, same shapes as the HTTP API ---------
    def _preset(self, scenario_id: str):
        from acme.api.scenarios import BY_ID

        if scenario_id not in BY_ID:
            raise EngineOperationError(f"unknown scenario {scenario_id}")
        return BY_ID[scenario_id]

    def _journey(self, journey_id: str):
        if journey_id not in self.runtime.journeys:
            raise EngineOperationError(f"unknown journey {journey_id}")
        return self.runtime.journeys[journey_id]

    def scenarios_list(self) -> list[dict[str, Any]]:
        from acme.api.scenarios import PRESETS, preset_view

        return [preset_view(p).model_dump(mode="json") for p in PRESETS]

    def scenario_start(self, scenario_id: str) -> dict[str, Any]:
        return self.scenarios.start(self._preset(scenario_id)).model_dump(mode="json")

    def scenario_state(self) -> dict[str, Any]:
        if self.scenarios.active is None:
            raise EngineOperationError("no scenario is started")
        return self.scenarios.state().model_dump(mode="json")

    async def scenario_step(self) -> dict[str, Any]:
        """Customer half of a step as an ADK turn. The Sales tick is driven by the demo."""
        runner = self.scenarios
        if runner.active is None:
            raise EngineOperationError("no scenario is started")
        if runner.index >= len(runner.active.steps):
            raise EngineOperationError(f"scenario {runner.active.id} is finished; start it again")
        sid = f"scenario-{runner.active.id}"
        if (
            await self._sessions.get_session(
                app_name=f"{APP_NAME}-scenario", user_id="demo", session_id=sid
            )
            is None
        ):
            await self._sessions.create_session(
                app_name=f"{APP_NAME}-scenario", user_id="demo", session_id=sid
            )
        turn = types.Content(role="user", parts=[types.Part(text="step")])
        async for _ in self._step_runner.run_async(
            user_id="demo", session_id=sid, new_message=turn
        ):
            pass
        return runner.state().model_dump(mode="json")

    def journeys_list(self) -> list[dict[str, Any]]:
        from acme.api.app import journey_summary

        return [journey_summary(r).model_dump(mode="json") for r in self.runtime.journeys.values()]

    async def journey_detail(self, journey_id: str) -> dict[str, Any]:
        from acme.api.app import journey_detail_view

        await self.runtime.tick()
        rec = self._journey(journey_id)
        return journey_detail_view(self.runtime, rec).model_dump(mode="json")

    def journey_events(self, journey_id: str, since: int = 0) -> list[dict[str, Any]]:
        """Timeline events from index ``since`` (a poll-friendly form of the SSE stream)."""
        timeline = self._journey(journey_id).state.timeline
        return [e.model_dump(mode="json") for e in timeline[max(since, 0) :]]

    # --- phone panel, reset and trigger (#181): the API's own functions, errors as data -------
    async def _api(self, fn, *args) -> dict[str, Any] | list[Any]:
        from fastapi import HTTPException

        try:
            out = await fn(self.runtime, *args)
        except HTTPException as exc:  # remote calls lose exception types; the backend re-raises
            return {"error": {"status": exc.status_code, "detail": exc.detail}}
        return (
            [m.model_dump(mode="json") for m in out]
            if isinstance(out, list)
            else (out.model_dump(mode="json"))
        )

    def reset(self) -> dict[str, str]:
        self.runtime.reset()
        return {"status": "reset"}

    async def trigger_cancellation(self, flight: str, reason: str) -> dict[str, Any]:
        from acme.api.app import do_trigger

        return await self._api(do_trigger, flight, reason)

    async def whatsapp_messages(self, passenger_id: str):
        from acme.api.app import do_messages

        return await self._api(do_messages, passenger_id)

    async def whatsapp_reply(self, passenger_id: str, text: str) -> dict[str, Any]:
        from acme.api.app import do_reply

        return await self._api(do_reply, passenger_id, text)

    async def whatsapp_action(self, passenger_id: str, action: str) -> dict[str, Any]:
        from acme.api.app import do_action

        return await self._api(do_action, passenger_id, action)

    def register_operations(self) -> dict[str, list[str]]:
        return {
            "": [
                "scenarios_list",
                "scenario_start",
                "scenario_state",
                "journeys_list",
                "journey_events",
                "reset",
            ],
            "stream": ["stream_query"],
            "async_stream": ["async_stream_query"],
            "async": [
                "a2a_skill",
                "scenario_step",
                "journey_detail",
                "trigger_cancellation",
                "whatsapp_messages",
                "whatsapp_reply",
                "whatsapp_action",
            ],
        }


def build_customer_engine_app() -> CustomerEngineApp:
    return CustomerEngineApp()
