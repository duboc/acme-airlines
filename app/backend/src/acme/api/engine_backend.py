"""`ENGINE_MODE=agent-engine`: the demo API served from the deployed Agent Engines (#164).

`EngineBackend` has the interface the scenario and journey routes use, and calls the Customer
engine's registered operations (#162). A scenario step is the Customer `scenario_step`, then a
turn on the Sales engine, so the Sales A2A pitch shows as its own engine trace. Engine calls use
ADC (`_AdcEngine`); tests route them to the local engine apps. Nothing here logs request or
response bodies (they carry passenger data).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

from acme.api.app import JourneyDetail, JourneySummary
from acme.api.events import TimelineEvent
from acme.api.scenarios import BY_ID, PresetView, ScenarioState

ENV_CUSTOMER, ENV_SALES = "AGENT_ENGINE_CUSTOMER", "AGENT_ENGINE_SALES"
ASYNC_OPS = {"scenario_step", "journey_detail", "a2a_skill"}  # registered with mode "async"
POLL_SECONDS = 0.5


class EngineConfigError(ValueError):
    """`ENGINE_MODE=agent-engine` without usable engine resource names."""


class EngineHandle(Protocol):
    async def call(self, op: str, **kwargs: Any) -> Any: ...
    def stream(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]: ...


class _AdcEngine:
    """A deployed engine, queried with ADC. Never constructed in CI."""

    def __init__(self, resource_name: str) -> None:
        import vertexai
        from vertexai import agent_engines

        _, project, _, location, *_ = resource_name.split("/")  # regional resource location
        vertexai.init(project=project, location=location)
        self._engine = agent_engines.get(resource_name)

    async def call(self, op: str, **kwargs: Any) -> Any:
        method = getattr(self._engine, op)
        if op in ASYNC_OPS:
            return await method(**kwargs)
        return await asyncio.to_thread(method, **kwargs)

    def stream(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        return self._engine.async_stream_query(**kwargs)


def _resource(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "")
    if not value.startswith("projects/") or "/reasoningEngines/" not in value:
        raise EngineConfigError(
            f"ENGINE_MODE=agent-engine needs {key} set to the full engine resource name "
            "(projects/<p>/locations/<l>/reasoningEngines/<id>); it is written by the deploy."
        )
    return value


class EngineBackend:
    def __init__(self, customer: EngineHandle, sales: EngineHandle) -> None:
        self._customer, self._sales = customer, sales
        self._active: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EngineBackend:
        env = os.environ if env is None else env
        customer, sales = _resource(env, ENV_CUSTOMER), _resource(env, ENV_SALES)
        return cls(_AdcEngine(customer), _AdcEngine(sales))

    async def scenarios_list(self) -> list[dict]:
        return await self._customer.call("scenarios_list")

    async def scenario_start(self, scenario_id: str) -> dict:
        state = await self._customer.call("scenario_start", scenario_id=scenario_id)
        self._active = scenario_id
        return state

    async def scenario_state(self) -> dict:
        return await self._customer.call("scenario_state")

    async def scenario_step(self, scenario_id: str) -> dict:
        """Customer step, then one Sales engine turn (its own trace). `409` conditions are the
        route's job: it checks `active` and `state["done"]` before calling this."""
        await self._customer.call("scenario_step")
        async for _ in self._sales.stream(message="tick", user_id="demo"):
            pass
        return await self.scenario_state()

    @property
    def active(self) -> str | None:
        return self._active

    async def journeys_list(self) -> list[dict]:
        return await self._customer.call("journeys_list")

    async def journey_detail(self, journey_id: str) -> dict:
        return await self._customer.call("journey_detail", journey_id=journey_id)

    async def journey_events(self, journey_id: str, since: int = 0) -> list[dict]:
        return await self._customer.call("journey_events", journey_id=journey_id, since=since)


def create_engine_app(backend: EngineBackend | None = None) -> FastAPI:
    """Same scenario and journey routes (and shapes) as `acme.api.app`, served from the engines."""
    backend = backend or EngineBackend.from_env()
    app = FastAPI(title="ACME Airlines runtime API (agent-engine mode)", version="0.1.0")
    app.state.engine_backend = backend

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "engine": "agent-engine"}

    @app.get("/scenarios", tags=["scenarios"])
    async def list_scenarios() -> list[PresetView]:
        return [PresetView(**p) for p in await backend.scenarios_list()]

    @app.post("/scenarios/{scenario_id}/start", tags=["scenarios"])
    async def start_scenario(scenario_id: str) -> ScenarioState:
        if scenario_id not in BY_ID:
            raise HTTPException(404, f"unknown scenario {scenario_id}")
        return ScenarioState(**await backend.scenario_start(scenario_id))

    @app.post("/scenarios/{scenario_id}/step", tags=["scenarios"])
    async def step_scenario(scenario_id: str) -> ScenarioState:
        if scenario_id not in BY_ID:
            raise HTTPException(404, f"unknown scenario {scenario_id}")
        if backend.active != scenario_id:
            raise HTTPException(409, f"scenario {scenario_id} is not started")
        if (await backend.scenario_state())["done"]:
            raise HTTPException(409, f"scenario {scenario_id} is finished; start it again")
        return ScenarioState(**await backend.scenario_step(scenario_id))

    @app.get("/journeys", tags=["journeys"])
    async def list_journeys() -> list[JourneySummary]:
        return [JourneySummary(**j) for j in await backend.journeys_list()]

    def known(journeys: list[dict], journey_id: str) -> None:
        if journey_id not in {j["journey_id"] for j in journeys}:
            raise HTTPException(404, f"unknown journey {journey_id}")

    @app.get("/journeys/{journey_id}", tags=["journeys"])
    async def journey_detail(journey_id: str) -> JourneyDetail:
        known(await backend.journeys_list(), journey_id)
        return JourneyDetail(**await backend.journey_detail(journey_id))

    @app.get(
        "/journeys/{journey_id}/events",
        tags=["journeys"],
        response_model=list[TimelineEvent],
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def journey_events(
        journey_id: str, limit: int | None = Query(None, ge=1)
    ) -> StreamingResponse:
        known(await backend.journeys_list(), journey_id)

        async def stream() -> AsyncIterator[str]:
            sent = 0
            while limit is None or sent < limit:
                batch = await backend.journey_events(journey_id, sent)
                for event in batch[: None if limit is None else limit - sent]:
                    sent += 1
                    yield f"data: {json.dumps(event)}\n\n"
                if not batch:
                    await asyncio.sleep(POLL_SECONDS)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
