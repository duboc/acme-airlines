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
import logging
import os
import re
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from acme.api.app import (
    ActionResult,
    JourneyDetail,
    JourneySummary,
    PassengerAction,
    PassengerReply,
    TriggerCancellation,
    TriggerResult,
)
from acme.api.events import TimelineEvent
from acme.api.scenarios import BY_ID, PresetView, ScenarioState
from acme.customer.runtime import WhatsAppMessage

ENV_CUSTOMER, ENV_SALES = "AGENT_ENGINE_CUSTOMER", "AGENT_ENGINE_SALES"
ASYNC_OPS = {
    "scenario_step",
    "journey_detail",
    "a2a_skill",
    "trigger_cancellation",
    "whatsapp_messages",
    "whatsapp_reply",
    "whatsapp_action",
}  # registered with mode "async"
POLL_SECONDS = 0.5
log = logging.getLogger(__name__)


class EngineConfigError(ValueError):
    """`ENGINE_MODE=agent-engine` without usable engine resource names."""


class EngineHandle(Protocol):
    async def call(self, op: str, **kwargs: Any) -> Any: ...
    def stream(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]: ...


class EngineUnavailable(RuntimeError):
    """An engine could not be reached or resolved (credentials, network); no detail is leaked."""


class _AdcEngine:
    """A deployed engine, queried with ADC. Lazy: nothing is resolved until the first call,
    so the demo starts without credentials (#199). Never constructed in CI."""

    def __init__(self, resource: str | Callable[[], str]) -> None:
        self._resource = resource
        self._engine: Any = None

    def _connect(self, resource_name: str) -> Any:
        import vertexai
        from vertexai import agent_engines

        _, project, _, location, *_ = resource_name.split("/")  # regional resource location
        vertexai.init(project=project, location=location)
        return agent_engines.get(resource_name)

    async def _get(self) -> Any:
        if self._engine is None:
            name = self._resource() if callable(self._resource) else self._resource
            try:
                self._engine = await asyncio.to_thread(self._connect, name)
            except Exception as e:  # not cached: the next request retries
                log.warning("engine connect failed: %s", type(e).__name__)
                raise EngineUnavailable("The Agent Engine is unavailable right now.") from e
        return self._engine

    async def call(self, op: str, **kwargs: Any) -> Any:
        method = getattr(await self._get(), op)
        if op in ASYNC_OPS:
            return await method(**kwargs)
        return await asyncio.to_thread(method, **kwargs)

    async def stream(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        async for item in (await self._get()).async_stream_query(**kwargs):
            yield item


def _resource(env: Mapping[str, str], key: str) -> str:
    """The full resource name, pinned to our project and the engine location (fail fast)."""
    project = re.escape(env.get("GOOGLE_CLOUD_PROJECT") or "acme-airlines-factory")
    location = re.escape(env.get("AGENT_ENGINE_LOCATION") or "us-central1")
    value = env.get(key, "")
    if not re.fullmatch(
        rf"projects/({project}|\d+)/locations/{location}/reasoningEngines/\d+", value
    ):
        raise EngineConfigError(
            f"ENGINE_MODE=agent-engine needs {key} set to the engine resource name "
            f"projects/<{project} or its number>/locations/{location}/reasoningEngines/<digits>; "
            "it is written by the deploy."
        )
    return value


def ok(reply: Any) -> Any:
    """Engine ops return API errors as data (`{"error": {"status", "detail"}}`): re-raise them."""
    if isinstance(reply, dict) and "error" in reply:
        raise HTTPException(reply["error"]["status"], reply["error"]["detail"])
    return reply


class EngineBackend:
    def __init__(self, customer: EngineHandle, sales: EngineHandle) -> None:
        self._customer, self._sales = customer, sales
        self._active: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EngineBackend:
        env = os.environ if env is None else env

        # Lazy (#199): names are validated and engines resolved on the first request, so a
        # missing ADC or a bad name is a clear 503 then, not a startup crash.
        def name(key: str) -> str:
            _resource(env, ENV_CUSTOMER), _resource(env, ENV_SALES)  # report any bad name first
            return env[key]

        return cls(_AdcEngine(lambda: name(ENV_CUSTOMER)), _AdcEngine(lambda: name(ENV_SALES)))

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

    async def reset(self) -> dict:
        self._active = None
        return await self._customer.call("reset")

    async def api(self, op: str, **kwargs: Any) -> Any:
        """Phone panel and trigger ops, which return API errors as data."""
        return ok(await self._customer.call(op, **kwargs))


def create_engine_app(backend: EngineBackend | None = None) -> FastAPI:
    """Same scenario and journey routes (and shapes) as `acme.api.app`, served from the engines."""
    backend = backend or EngineBackend.from_env()
    app = FastAPI(title="ACME Airlines runtime API (agent-engine mode)", version="0.1.0")
    app.state.engine_backend = backend

    @app.exception_handler(EngineConfigError)
    async def config_error(_: Request, e: EngineConfigError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=503)

    @app.exception_handler(EngineUnavailable)
    async def unavailable(_: Request, e: EngineUnavailable) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=503)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "engine": "agent-engine"}

    @app.post("/scenario/reset", tags=["scenario"])
    async def reset() -> dict[str, str]:
        return await backend.reset()

    @app.post("/scenario/trigger-cancellation", tags=["scenario"])
    async def trigger_cancellation(body: TriggerCancellation) -> TriggerResult:
        return TriggerResult(**await backend.api("trigger_cancellation", **body.model_dump()))

    @app.get("/whatsapp/{passenger_id}/messages", tags=["whatsapp"])
    async def whatsapp_messages(passenger_id: str) -> list[WhatsAppMessage]:
        msgs = await backend.api("whatsapp_messages", passenger_id=passenger_id)
        return [WhatsAppMessage(**m) for m in msgs]

    @app.post("/whatsapp/{passenger_id}/messages", status_code=201, tags=["whatsapp"])
    async def whatsapp_reply(passenger_id: str, body: PassengerReply) -> WhatsAppMessage:
        reply = await backend.api("whatsapp_reply", passenger_id=passenger_id, text=body.text)
        return WhatsAppMessage(**reply)

    @app.post("/whatsapp/{passenger_id}/actions", tags=["whatsapp"])
    async def whatsapp_action(passenger_id: str, body: PassengerAction) -> ActionResult:
        reply = await backend.api("whatsapp_action", passenger_id=passenger_id, action=body.action)
        return ActionResult(**reply)

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
