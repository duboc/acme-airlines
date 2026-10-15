"""`ENGINE_MODE=agent-engine`: the demo API served from the deployed Agent Engines (#164).

`EngineBackend` has the interface the scenario and journey routes use, and calls the Customer
engine's registered operations (#162). A scenario step is the Customer `scenario_step`, then a
turn on the Sales engine, so the Sales A2A pitch shows as its own engine trace. Engine calls use
ADC (`_AdcEngine`); tests route them to the local engine apps. Nothing here logs request or
response bodies (they carry passenger data).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Protocol

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from acme import metrics
from acme.api.app import (
    ActionResult,
    JourneyDetail,
    JourneySummary,
    PassengerAction,
    PassengerReply,
    TriggerCancellation,
    TriggerResult,
)
from acme.api.errors import ApiError, add_error_handlers, not_found
from acme.api.events import TimelineEvent
from acme.api.scenarios import BY_ID, PresetView, ScenarioState
from acme.api.sse import event_stream, resume_from
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
POLL_SECONDS = 2.0  # a stream looks for news this often; the engine is asked less (#335)
TIMELINE_BASE_SECONDS = 1.6  # the engine is asked for a journey's news at most this often...
TIMELINE_MAX_SECONDS = 6.0  # ...backing off to this when nothing changes: <= 30 queries/minute
RETRY_BACKOFF = (0.2, 0.6)  # seconds between the attempts of an idempotent read (#306), jittered
RETRY_AFTER_SECONDS = 5  # what a client is told to wait after an engine error (HTTP Retry-After)
QUOTA_RETRY_AFTER_SECONDS = 20  # the per-minute Agent Engine query quota refills within a minute
JOURNEYS_TTL_SECONDS = 2.0  # how long a journeys list is reused to validate an id (#335)
READ_OPS = {
    "scenarios_list", "scenario_state", "journeys_list", "journey_detail", "journey_events",
    "whatsapp_messages",
}  # fmt: skip  # safe to repeat; everything else may have had an effect and is never retried
log = logging.getLogger(__name__)


class EngineConfigError(ValueError):
    """`ENGINE_MODE=agent-engine` without usable engine resource names."""


class EngineHandle(Protocol):
    async def call(self, op: str, **kwargs: Any) -> Any: ...
    def stream(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]: ...


class EngineUnavailable(RuntimeError):
    """An engine could not be reached or resolved (credentials, network); no detail is leaked."""

    retry_after = RETRY_AFTER_SECONDS


class EngineQuotaExceeded(EngineUnavailable):
    """gRPC RESOURCE_EXHAUSTED (the per-minute Agent Engine query quota): HTTP 429. The request
    never reached the engine, so it is safe to repeat later; retrying at once would only burn more
    quota, so it is not retried here."""

    retry_after = QUOTA_RETRY_AFTER_SECONDS


def is_quota_error(exc: BaseException) -> bool:
    """gRPC RESOURCE_EXHAUSTED or HTTP 429, whatever client library raised it (duck-typed)."""
    code = getattr(exc, "code", None)
    code = code() if callable(code) else code
    return (
        type(exc).__name__ in ("ResourceExhausted", "TooManyRequests")
        or "RESOURCE_EXHAUSTED" in str(code)
        or code == 429
    )


class StepApplied(EngineUnavailable):
    """The Customer step committed but its new state could not be read: refresh, do not repeat."""


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
        """One engine op. Reads are retried with a short bounded backoff (a transient RPC error is
        common on Agent Engine); any other op is never repeated, as it may have taken effect. A
        failure is an `EngineUnavailable` (503), never a raw exception (500) (#306)."""
        attempts = len(RETRY_BACKOFF) + 1 if op in READ_OPS else 1
        for attempt in range(attempts):
            try:
                method = getattr(await self._get(), op)
                if op in ASYNC_OPS:
                    return await method(**kwargs)
                return await asyncio.to_thread(method, **kwargs)
            except (EngineUnavailable, EngineConfigError):
                raise
            except Exception as e:
                log.warning("engine %s failed (attempt %d): %s", op, attempt + 1, type(e).__name__)
                if is_quota_error(e):  # a retry burns more quota: tell the client to wait
                    raise EngineQuotaExceeded(
                        f"The Agent Engine query quota is exhausted ({op}); retry shortly."
                    ) from e
                if attempt + 1 >= attempts:
                    raise EngineUnavailable(
                        f"The Agent Engine call {op} failed ({type(e).__name__}); try again."
                    ) from e
                await asyncio.sleep(RETRY_BACKOFF[attempt] * (0.5 + random.random()))  # jitter
        raise AssertionError("unreachable")  # pragma: no cover

    async def stream(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        try:
            async for item in (await self._get()).async_stream_query(**kwargs):
                yield item
        except (EngineUnavailable, EngineConfigError):
            raise
        except Exception as e:  # a stream is not repeatable: it may have had an effect
            log.warning("engine stream failed: %s", type(e).__name__)
            if is_quota_error(e):
                raise EngineQuotaExceeded("The Agent Engine query quota is exhausted.") from e
            raise EngineUnavailable(
                f"The Agent Engine stream failed ({type(e).__name__}); try again."
            ) from e


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
        err = reply["error"]
        raise ApiError(err["status"], err.get("code", "error"), err["detail"])
    return reply


class EngineBackend:
    def __init__(self, customer: EngineHandle, sales: EngineHandle) -> None:
        self._customer, self._sales = customer, sales
        self._active: str | None = None
        self._step = 0  # the caller-side step counter sent with every call (#266)
        self._tape: list[Any] | None = None  # the replay tape the engine returned (#279)
        self._run = 0  # bumped by every scenario start and reset: ends the open streams (#305)
        # One engine query per journey per poll interval, however many streams are open (#335).
        self._journeys: tuple[float, list[dict]] = (float("-inf"), [])
        self._timelines: dict[tuple[int, str], tuple[float, list[dict], float]] = {}
        self._timeline_locks: dict[tuple[int, str], asyncio.Lock] = {}
        self._journeys_lock = asyncio.Lock()

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

    def _ctx(self) -> dict[str, Any]:
        """Where the scenario is, on every call: a container that does not hold that state (another
        replica, a restart) rebuilds it deterministically instead of failing (#266)."""
        if self._active is None:
            return {}
        ctx: dict[str, Any] = {"preset": self._active, "step": self._step}
        if self._tape is not None:  # engines without a tape (older deploys) never get the key
            ctx["tape"] = self._tape
        return ctx

    def _keep_tape(self, state: dict) -> dict:
        """Remember the engine's replay tape and hand the plain state on: the tape holds passenger
        text and goes back only to the Customer engine (never to Sales or the API client)."""
        tape = state.pop("tape", None)
        self._tape = tape if isinstance(tape, list) else None
        return state

    @property
    def run(self) -> int:
        return self._run

    async def scenario_start(self, scenario_id: str) -> dict:
        self._run += 1  # before the call: a stream polling meanwhile stops using the old context
        self._stir()
        state = await self._customer.call("scenario_start", scenario_id=scenario_id)
        self._active, self._step = scenario_id, state["step_index"]
        return self._keep_tape(state)

    async def scenario_state(self) -> dict:
        state = await self._customer.call("scenario_state", **self._ctx())
        # Never backwards: a stale read from another worker must not make the next call re-send
        # a step that already ran (only scenario_start and reset set the counter freely).
        self._step = max(self._step, state["step_index"])
        return self._keep_tape(state)

    async def scenario_step(self, scenario_id: str) -> dict:
        """Customer step, then one Sales engine turn (its own trace). `409` conditions are the
        route's job: it checks `active` and `state["done"]` before calling this."""
        await self._customer.call("scenario_step", **self._ctx())
        # The step is committed in the engine: the counter follows it, whatever comes next.
        self._step += 1
        self._stir()
        try:
            async for _ in self._sales.stream(message="tick", user_id="demo"):
                pass
        except Exception as e:  # noqa: BLE001 - the tick is best effort; the next step ticks again
            # (The tick's own a2a_skill calls carry no preset/step context: Sales cannot send it.)
            log.warning("sales tick failed after a committed step: %s", type(e).__name__)
        try:
            return await self.scenario_state()
        except EngineUnavailable as e:
            raise StepApplied(
                "The step was applied but its state could not be read: refresh."
            ) from e

    @property
    def active(self) -> str | None:
        return self._active

    async def journeys_list(self) -> list[dict]:
        return await self._customer.call("journeys_list", **self._ctx())

    async def journey_detail(self, journey_id: str) -> dict:
        return await self._customer.call("journey_detail", journey_id=journey_id, **self._ctx())

    async def known_journeys(self) -> list[dict]:
        """The journeys list, reused for `JOURNEYS_TTL_SECONDS`: opening N streams (or reconnects)
        in a row costs one engine query, not N (#335)."""
        async with self._journeys_lock:
            at, journeys = self._journeys
            if time.monotonic() - at >= JOURNEYS_TTL_SECONDS:
                journeys = await self.journeys_list()
                self._journeys = (time.monotonic(), journeys)
            return journeys

    async def timeline(self, journey_id: str) -> list[dict]:
        """The events of a journey so far, shared by every stream of this run: the engine is asked
        for the new ones at most once per interval, whatever the number of streams, and the interval
        grows while nothing changes (a step, reply or action resets it) (#335)."""
        key = (self._run, journey_id)
        for old in [k for k in self._timelines if k[0] != self._run]:
            self._timelines.pop(old, None)  # a restart ended those runs
            self._timeline_locks.pop(old, None)
        lock = self._timeline_locks.setdefault(key, asyncio.Lock())
        async with lock:
            at, events, interval = self._timelines.get(
                key, (float("-inf"), [], TIMELINE_BASE_SECONDS)
            )
            if time.monotonic() - at >= interval:
                new = await self.journey_events(journey_id, len(events))
                events = events + new
                interval = (
                    TIMELINE_BASE_SECONDS if new else min(interval * 1.5, TIMELINE_MAX_SECONDS)
                )
                self._timelines[key] = (time.monotonic(), events, interval)
            return events

    def _stir(self) -> None:
        """Something happened (a step, a reply, an action): look again soon."""
        self._journeys = (float("-inf"), [])
        self._timelines = {
            k: (float("-inf"), ev, TIMELINE_BASE_SECONDS)
            for k, (_, ev, _) in self._timelines.items()
        }

    async def journey_events(self, journey_id: str, since: int = 0) -> list[dict]:
        return await self._customer.call(
            "journey_events", journey_id=journey_id, since=since, **self._ctx()
        )

    async def reset(self) -> dict:
        self._run += 1
        self._stir()
        self._active = None
        return await self._customer.call("reset")

    async def api(self, op: str, **kwargs: Any) -> Any:
        """Phone panel and trigger ops, which return API errors as data."""
        ctx = self._ctx() if op.startswith("whatsapp_") else {}  # not trigger_cancellation
        reply = ok(await self._customer.call(op, **kwargs, **ctx))
        if op != "whatsapp_messages":  # a write: the open streams should look again soon
            self._stir()
        return reply


async def _timeline_after(backend: EngineBackend, journey_id: str, sent: int) -> list[dict]:
    return (await backend.timeline(journey_id))[sent:]


def create_engine_app(backend: EngineBackend | None = None) -> FastAPI:
    """Same scenario and journey routes (and shapes) as `acme.api.app`, served from the engines."""
    backend = backend or EngineBackend.from_env()
    app = FastAPI(title="ACME Airlines runtime API (agent-engine mode)", version="0.1.0")
    app.state.engine_backend = backend
    add_error_handlers(app)
    metrics.add_http_metrics(app)

    @app.exception_handler(EngineConfigError)
    async def config_error(_: Request, e: EngineConfigError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=503)

    @app.exception_handler(EngineUnavailable)
    async def unavailable(_: Request, e: EngineUnavailable) -> JSONResponse:
        status = 429 if isinstance(e, EngineQuotaExceeded) else 503  # never a bare 500 (#306/#335)
        return JSONResponse(
            {"detail": str(e)}, status_code=status, headers={"Retry-After": str(e.retry_after)}
        )

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
            raise not_found("scenario", scenario_id)
        return ScenarioState(**await backend.scenario_start(scenario_id))

    @app.post("/scenarios/{scenario_id}/step", tags=["scenarios"])
    async def step_scenario(scenario_id: str) -> ScenarioState:
        if scenario_id not in BY_ID:
            raise not_found("scenario", scenario_id)
        if backend.active != scenario_id:
            raise ApiError(409, "scenario_not_started", "scenario is not started")
        if (await backend.scenario_state())["done"]:
            raise ApiError(409, "scenario_finished", "scenario is finished; start it again")
        return ScenarioState(**await backend.scenario_step(scenario_id))

    @app.get("/journeys", tags=["journeys"])
    async def list_journeys() -> list[JourneySummary]:
        return [JourneySummary(**j) for j in await backend.journeys_list()]

    def known(journeys: list[dict], journey_id: str) -> None:
        if journey_id not in {j["journey_id"] for j in journeys}:
            raise not_found("journey", journey_id)

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
        request: Request, journey_id: str, limit: int | None = Query(None, ge=1)
    ) -> StreamingResponse:
        known(await backend.known_journeys(), journey_id)
        run = backend.run  # the stream belongs to this run: a restart ends it (#305)
        stream = event_stream(
            request,
            lambda sent: _timeline_after(backend, journey_id, sent),
            limit=limit,
            poll_seconds=POLL_SECONDS,
            still_open=lambda: backend.run == run,
            start_after=resume_from(request.headers.get("last-event-id")),
        )
        return StreamingResponse(stream, media_type="text/event-stream")

    return app
