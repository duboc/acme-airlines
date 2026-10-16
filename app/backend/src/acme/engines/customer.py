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

import asyncio
import concurrent.futures
import contextlib
import functools
import inspect
import logging
import os
import time
from collections.abc import AsyncGenerator, AsyncIterator, Iterator, Mapping
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types
from opentelemetry import trace
from pydantic import ConfigDict

from acme import metrics
from acme.diagnostics import format_error
from acme.llm.config import LlmConfigError, load_llm_mode, load_vertex_config
from acme.llm.tape import RecordingLlm, TapeLlm, clean_tape, manual_turns
from acme.replay import replaying
from acme.sim.client import reuse_sessions, warm_registry
from acme.telemetry import EngineOp, diag, flush, process_identity, span

log = logging.getLogger(__name__)

DISPLAY_NAME = "acme-customer-agent"
APP_NAME = "acme-customer"
# Exact pins for direct and critical runtime transitive packages, kept equal to uv.lock by a
# test: the container resolves these with pip, so floating ranges let it drift from what CI
# tested (#185, #330).
ENGINE_REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent-engines]==1.165.1",
    "google-adk[a2a]==1.39.1",
    "google-genai==2.30.1",
    "mcp==1.30.0",
    "opentelemetry-api==1.41.1",
    "opentelemetry-sdk==1.41.1",
    "opentelemetry-exporter-gcp-trace==1.15.0",
    "opentelemetry-exporter-gcp-monitoring==1.15.0a0",
    "httpx==0.28.1",
    "pydantic==2.14.0",
    "fastapi==0.141.1",
    "a2a-sdk==0.3.26",
    "anyio==4.15.1",
    "certifi==2026.7.22",
    "cloudpickle==3.1.2",
    "cryptography==50.0.2",
    "grpcio==1.84.0",
    "httpcore==1.0.9",
    "protobuf==6.33.6",
    "pydantic-core==2.50.0",
    "starlette==1.7.0",
    "urllib3==2.8.0",
    "uvicorn==0.54.0",
]


class EngineConfigError(ValueError):
    """The engine environment is invalid (e.g. a non-global Vertex location)."""


def engine_requirements() -> list[str]:
    """`requirements=` for `agent_engines.create/update` (the deploy issue uses this)."""
    return list(ENGINE_REQUIREMENTS)


def engine_extra_packages() -> list[str]:
    """`extra_packages=` for `agent_engines.create/update`, relative to `deploy.package_root()`.

    The SDK tars each path as given (`tar.add(path)`), so the tarball must hold `acme/...` at its
    root for `import acme` to work in the container (#185). Hence "acme", not "src/acme"; the
    deploy runs from the directory that contains the package.
    """
    return ["acme"]


def validate_env(env: Mapping[str, str] | None = None) -> str:
    """Fail fast on a bad environment; returns the LLM mode. Vertex means global, ADC, no keys."""
    env = os.environ if env is None else env
    try:
        mode = load_llm_mode(env)
        if mode == "vertex":
            if not (env.get("ACME_GENAI_PROJECT") or env.get("GOOGLE_CLOUD_PROJECT")):
                raise EngineConfigError(
                    "GOOGLE_CLOUD_PROJECT (injected by the platform) or ACME_GENAI_PROJECT "
                    "must be set when LLM_MODE=vertex"
                )
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


STATE_DB_ENV = "ACME_STATE_DB"


def _run_blocking(coro):
    """Run a coroutine from a sync operation, whether or not this thread has an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


_REASONS = {"NoSentOffer": "no offer sent for journey", "AssertionError": "invariant violated"}


def describe_error(exc: BaseException) -> EngineOperationError:
    """Never-empty, PII-free error for the RPC boundary (#266): exception class, a fixed reason
    and the innermost code location. The exception's own text is NOT used (it may hold ids)."""
    name = type(exc).__name__
    tb = exc.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    where = f" at {os.path.basename(tb.tb_frame.f_code.co_filename)}:{tb.tb_lineno}" if tb else ""
    return EngineOperationError(f"{name}: {_REASONS.get(name, 'operation failed')}{where}")


def shared(fn):
    """Run an engine operation under the cross-process shared state (#200), if configured.

    Agent Engine runs several processes per container, so a turn may land in a process that did
    not handle the previous one: each operation loads the newest snapshot, runs while holding the
    cross-process lock (this is also what keeps the per-journey guarantees of #88 and #97 true
    across processes) and writes a new snapshot if anything changed. Without `ACME_STATE_DB`
    (local runs, unit tests) operations are unchanged.
    """
    if inspect.isasyncgenfunction(fn):

        @functools.wraps(fn)
        async def wrapped_gen(self, *args, **kwargs):
            op = EngineOp(fn.__name__)
            try:
                with op.span():
                    async with self._shared_operation():
                        await self._ensure_scenario(kwargs)
                        async for item in fn(self, *args, **kwargs):
                            yield item
            except EngineOperationError:
                raise
            except Exception as exc:
                metrics.http_5xx(500)  # an unexpected failure of an operation (#344)
                raise describe_error(exc) from exc
            finally:
                op.log(flush(), **self._op_context())  # flush before the process is throttled

        return wrapped_gen
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def wrapped_async(self, *args, **kwargs):
            op = EngineOp(fn.__name__)
            try:
                with op.span():
                    async with self._shared_operation():
                        await self._ensure_scenario(kwargs)
                        return await fn(self, *args, **kwargs)
            except EngineOperationError:
                raise
            except Exception as exc:
                metrics.http_5xx(500)  # an unexpected failure of an operation (#344)
                raise describe_error(exc) from exc
            finally:
                op.log(await asyncio.to_thread(flush), **self._op_context())

        return wrapped_async

    @functools.wraps(fn)
    def wrapped(self, *args, **kwargs):
        op = EngineOp(fn.__name__)
        try:
            with op.span(), self._shared_operation_sync():
                _run_blocking(self._ensure_scenario(kwargs))
                return fn(self, *args, **kwargs)
        except EngineOperationError:
            raise
        except Exception as exc:
            metrics.http_5xx(500)  # an unexpected failure of an operation (#344)
            raise describe_error(exc) from exc
        finally:
            op.log(flush(), **self._op_context())

    return wrapped


def integral(value: Any, what: str) -> int:
    """An int argument as it arrives through Agent Engine: JSON numbers become doubles (protobuf
    Struct), so `5.0` is 5. Anything not whole (5.5, True, "5") is rejected."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EngineOperationError(f"bad {what}")
    if isinstance(value, float) and not (value.is_integer() and abs(value) < 2**53):
        raise EngineOperationError(f"bad {what}")
    return int(value)


class CustomerEngineApp:
    _STARTED = ("runtime", "skills", "scenarios", "_sessions", "_runner", "_step_runner")

    def __init__(self) -> None:
        self.llm_mode = validate_env()
        self.startup_error: str | None = None
        self.started = False
        self.last_rebuild: dict[str, Any] | None = None
        self.shared: Any = None  # SharedState when ACME_STATE_DB is set (built in set_up)

    def _shared_operation(self):
        if self.shared is None:
            return contextlib.nullcontext()
        return self.shared.operation(self.runtime, self.scenarios)

    def _shared_operation_sync(self):
        if self.shared is None:
            return contextlib.nullcontext()
        return self.shared.operation_sync(self.runtime, self.scenarios)

    def __getattr__(self, name: str) -> Any:
        # Only reached when normal lookup fails: an operation touched state that set_up() never
        # built. Say why instead of an AttributeError (pickling probes dunders: stay quiet).
        if name in CustomerEngineApp._STARTED:
            if self.startup_error:
                raise EngineOperationError(
                    "the engine failed to start; call `diagnostics` for the error"
                )
            raise EngineOperationError("the engine is not started yet (set_up() has not run)")
        raise AttributeError(name)

    def set_up(self) -> None:
        """Runs once inside the engine. A failure is captured (scrubbed) and exposed by
        `diagnostics` instead of killing the server, and the deploy fails on it (#185)."""
        try:
            self._set_up()
            self.started, self.startup_error = True, None
        except Exception as exc:  # noqa: BLE001 - reported, never silent: see deploy.verify_started
            self.started, self.startup_error = False, format_error(exc)
            logging.getLogger(__name__).error("engine start-up failed: %s", self.startup_error)

    def _op_context(self) -> dict[str, Any]:
        """Scenario and shared-state facts for the `ACME_DIAG` op line (#266): which scenario this
        process believes is active, and the snapshot versions, so a 'no scenario' can be matched
        to a process that never saw `scenario_start`."""
        runner = self.__dict__.get("scenarios")
        active = runner.active if runner is not None else None
        ctx: dict[str, Any] = {
            "scenario": active.id if active else None,
            "step": runner.index if active else None,
            "shared_version": None,
            "loaded_version": None,
            "db_inode": None,
        }
        runtime = self.__dict__.get("runtime")
        if runtime is not None and runtime.last_parse:  # the kind of the last parse, never text
            ctx.update(runtime.last_parse)
            runtime.last_parse = None
        if self.shared is not None:
            try:
                ctx["shared_version"] = self.shared.version()
                ctx["loaded_version"] = self.shared._loaded_version  # noqa: SLF001
                ctx["db_inode"] = os.stat(self.shared.path).st_ino
            except Exception:  # noqa: BLE001 - diagnostics must never break an operation
                pass
        return ctx

    async def _ensure_scenario(self, kwargs: dict[str, Any]) -> None:
        """Rebuild the scenario when this container is not at the caller's `preset` and `step`.

        The caller (the demo backend, the live proof) says where the scenario is on every call.
        State lives in container-local /tmp, so a call routed to another replica or a restarted
        container finds none; instead of failing it replays steps 0..step-1 from the seeded mocks
        (#266, ADR 0103). The replay is deterministic and invisible outside: the model is replaced
        by the offline one, and no spans or metrics are emitted. Callers that pass no context
        behave as before.
        """
        preset, step = kwargs.get("preset"), kwargs.get("step")
        if preset is None or step is None:
            return
        step = integral(step, "scenario step")  # Agent Engine sends 5.0 for 5
        tape = clean_tape(kwargs.get("tape"))  # untrusted: a bad tape is no tape
        runner = self.scenarios
        local_manual = manual_turns(getattr(self.runtime.llm, "entries", None))
        if (
            runner.active is not None
            and runner.active.id == preset
            and runner.index == step
            and (tape is None or local_manual == manual_turns(tape))
        ):
            return
        target = self._preset(preset)
        if not 0 <= step <= len(target.steps):
            raise EngineOperationError("bad scenario step")
        if runner.active is None:
            reason = "no_local_scenario"
        elif runner.active.id != preset:
            reason = "preset_mismatch"
        else:
            reason = "step_mismatch"
        from_step = runner.index if runner.active is not None else None
        started = time.perf_counter()
        recorder = self.runtime.llm  # the RecordingLlm wrapped around the engine's model
        discarded = 0
        for attempt in [tape, []] if tape else [[]]:
            replay_llm = TapeLlm(attempt)
            self.runtime.llm = replay_llm  # deterministic and free: never the deployed model
            try:
                with replaying():
                    runner.start(target)
                    async with reuse_sessions():  # one sim session per server for the replay
                        turns = manual_turns(attempt)
                        await self._replay_manual(runner, replay_llm, turns, 0)
                        for s in range(step):
                            await runner.step(sales_tick=True)
                            await self._replay_manual(runner, replay_llm, turns, s + 1)
                break
            except Exception:
                if not attempt:
                    raise  # the pure offline replay failed: a real error, not a tape problem
                discarded = 1  # a tape that does not fit the flow must never crash a rebuild
            finally:
                self.runtime.llm = recorder
        recorder.entries = replay_llm.entries  # what was replayed is what a next rebuild replays
        elapsed = time.perf_counter() - started
        metrics.rebuild_duration(elapsed, preset, reason)  # outside replaying(): not suppressed
        self.last_rebuild = {"preset": preset, "to_step": step, "reason": reason,
                             "ms": round(elapsed * 1000, 1)}  # fmt: skip
        ident = process_identity()
        diag(
            "rebuild", preset=preset, from_step=from_step, to_step=step, reason=reason,
            ms=round(elapsed * 1000, 1), tape_miss=replay_llm.misses, tape_discarded=discarded,
            proc=ident["proc"], container=ident["container"], pid=ident["pid"],
        )  # fmt: skip

    async def _replay_manual(
        self, runner: Any, replay_llm: TapeLlm, turns: list[dict[str, Any]], step: int
    ) -> None:
        from acme.api.app import do_action, do_reply

        for entry in turns:
            if entry["step"] != step:
                continue
            if entry["m"] == "manual_reply":
                await do_reply(runner.rt, entry["pid"], entry["text"])
                await runner.radar.tick()
                replay_llm.record_manual("whatsapp_reply", entry["pid"], entry["text"], step)
            else:
                await do_action(runner.rt, entry["pid"], entry["action"])
                replay_llm.record_manual("whatsapp_action", entry["pid"], entry["action"], step)

    def _no_scenario(self) -> EngineOperationError:
        """The readable error: ids and counters of the process that answered (no PII)."""
        ident, ctx = process_identity(), self._op_context()
        db = "none" if ctx["db_inode"] is None else (
            f"ino{ctx['db_inode']}:v{ctx['shared_version']}/loaded{ctx['loaded_version']}"
        )  # fmt: skip
        return EngineOperationError(
            "no scenario is started "
            f"(container={ident['container']} proc={ident['proc']} pid={ident['pid']} "
            f"scenario=none db={db})"
        )

    def diagnostics(self) -> dict[str, Any]:
        """Start-up status for the deploy check and operators (not an A2A skill)."""
        status = "ready" if self.started else ("failed" if self.startup_error else "starting")
        return {
            "engine": DISPLAY_NAME,
            "status": status,
            "startup_error": self.startup_error,
            "llm_mode": self.llm_mode,
            "identity": process_identity(),
            "last_rebuild": self.last_rebuild,  # None until this container rebuilt a scenario
        }

    def _set_up(self) -> None:
        """Build the runtime, agent and runner (also called directly by tests)."""
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from acme.api.scenarios import ScenarioRunner
        from acme.customer.adk_agent import build_customer_agent
        from acme.customer.runtime import CustomerRuntime
        from acme.customer.skills import CustomerSkills
        from acme.logging_setup import configure_logging
        from acme.metrics import configure_cloud_monitoring
        from acme.telemetry import configure_cloud_trace

        configure_logging()  # no passenger PII in Cloud Logging (#170)
        configure_cloud_trace(DISPLAY_NAME)
        configure_cloud_monitoring(DISPLAY_NAME)
        self.llm_mode = validate_env()  # refresh in the unpickled container environment (#440)
        self.runtime = CustomerRuntime()  # in-process simulators, LlmPort from LLM_MODE
        self.runtime.llm = RecordingLlm(self.runtime.llm)  # the replay tape (#279)
        warm_registry()  # build the sim tool registries once, not during a rebuild (#280)
        self.skills = CustomerSkills(self.runtime)
        self._sessions = InMemorySessionService()
        self._runner = Runner(
            app_name=APP_NAME, agent=build_customer_agent(self.runtime),
            session_service=self._sessions,
        )  # fmt: skip
        self.scenarios = ScenarioRunner(self.runtime)
        if state_db := os.environ.get(STATE_DB_ENV):
            from acme.state.shared import SharedState

            self.shared = SharedState(state_db)
        self._step_runner = Runner(
            app_name=f"{APP_NAME}-scenario",
            agent=ScenarioStepAgent(name="scenario_step", scenarios=self.scenarios),
            session_service=self._sessions,
        )

    @shared
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

    @shared
    async def a2a_skill(self, skill: str, args: dict[str, Any]) -> dict[str, Any]:
        """Same dispatch as HTTP `/a2a`: only the three allowlisted Sales skills (`list_journeys`,
        `journey_status`, `propose_offer`), never `_with_tape` or passenger PII (#330)."""
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
            log.info("unknown scenario %s", scenario_id)
            raise EngineOperationError("scenario not found")
        return BY_ID[scenario_id]

    def _journey(self, journey_id: str):
        if journey_id not in self.runtime.journeys:
            log.info("unknown journey %s", journey_id)
            raise EngineOperationError("journey not found")
        return self.runtime.journeys[journey_id]

    def _with_tape(self, state: Any) -> dict[str, Any]:
        """The scenario state plus the replay tape, for the caller only (see acme.llm.tape)."""
        return {**state.model_dump(mode="json"), "tape": list(self.runtime.llm.entries)}

    def scenarios_list(self) -> list[dict[str, Any]]:
        from acme.api.scenarios import PRESETS, preset_view

        return [preset_view(p).model_dump(mode="json") for p in PRESETS]

    @shared
    def scenario_start(self, scenario_id: str) -> dict[str, Any]:
        self.runtime.llm.reset()  # a new scenario starts a new tape
        return self._with_tape(self.scenarios.start(self._preset(scenario_id)))

    @shared
    def scenario_state(
        self, preset: str | None = None, step: int | None = None, tape: list[Any] | None = None
    ) -> dict[str, Any]:
        if self.scenarios.active is None:
            raise self._no_scenario()
        return self._with_tape(self.scenarios.state())

    @shared
    async def scenario_step(
        self, preset: str | None = None, step: int | None = None, tape: list[Any] | None = None
    ) -> dict[str, Any]:
        """Customer half of a step as an ADK turn. The Sales tick is driven by the demo."""
        runner = self.scenarios
        if runner.active is None:
            raise self._no_scenario()
        if runner.index >= len(runner.active.steps):
            raise EngineOperationError("scenario is finished; start it again")
        # A span this code owns carries the scenario for EVERY turn (cold first turn included):
        # `trace.get_current_span()` inside the ADK agent is not guaranteed to be recording (#215).
        with span("acme.scenario.step", acme__scenario=runner.active.id, acme__step=runner.index):
            return await self._run_scenario_step(runner)

    async def _run_scenario_step(self, runner: Any) -> dict[str, Any]:
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
        return self._with_tape(runner.state())

    @shared
    def journeys_list(
        self, preset: str | None = None, step: int | None = None, tape: list[Any] | None = None
    ) -> list[dict[str, Any]]:
        from acme.api.app import journey_summary

        return [journey_summary(r).model_dump(mode="json") for r in self.runtime.journeys.values()]

    @shared
    async def journey_detail(
        self,
        journey_id: str,
        preset: str | None = None,
        step: int | None = None,
        tape: list[Any] | None = None,
    ) -> dict[str, Any]:
        from acme.api.app import journey_detail_view

        await self.runtime.tick()
        rec = self._journey(journey_id)
        return journey_detail_view(self.runtime, rec).model_dump(mode="json")

    @shared
    async def journey_events(
        self,
        journey_id: str,
        since: int = 0,
        preset: str | None = None,
        step: int | None = None,
        tape: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Timeline events from index ``since`` (a poll-friendly form of the SSE stream)."""
        await self.runtime.tick()  # a poll is what moves time-triggered steps on (#395)
        timeline = self._journey(journey_id).state.timeline
        return [e.model_dump(mode="json") for e in timeline[max(integral(since, "since"), 0) :]]

    # --- phone panel, reset and trigger (#181): the API's own functions, errors as data -------
    async def _api(self, fn, *args) -> dict[str, Any] | list[Any]:
        from fastapi import HTTPException

        from acme.api.errors import ApiError

        try:
            out = await fn(self.runtime, *args)
        except HTTPException as exc:  # remote calls lose exception types; the backend re-raises
            if exc.status_code >= 500:
                metrics.http_5xx(exc.status_code)
            code = exc.code if isinstance(exc, ApiError) else "error"
            return {"error": {"status": exc.status_code, "detail": exc.detail, "code": code}}
        except Exception:  # noqa: BLE001 - nothing raw (SimError, KeyError...) crosses the RPC
            metrics.http_5xx(500)
            log.exception("engine operation failed")  # the log filter redacts the payload (#233)
            return {"error": {"status": 500, "detail": "internal error", "code": "internal_error"}}
        return (
            [m.model_dump(mode="json") for m in out]
            if isinstance(out, list)
            else (out.model_dump(mode="json"))
        )

    @shared
    def reset(self) -> dict[str, str]:
        self.scenarios.reset()  # world, clock and preset cursor (#321)
        self.runtime.llm.reset()  # a reset drops the tape with the state (#279)
        return {"status": "reset"}

    @shared
    async def trigger_cancellation(self, flight: str, reason: str) -> dict[str, Any]:
        from acme.api.app import do_trigger

        if self.scenarios.active is not None:  # a manual trigger ends the preset and its clock
            self.scenarios.reset()
        return await self._api(do_trigger, flight, reason)

    @shared
    async def whatsapp_messages(
        self,
        passenger_id: str,
        preset: str | None = None,
        step: int | None = None,
        tape: list[Any] | None = None,
    ):
        from acme.api.app import do_messages

        await self.runtime.tick()  # the 6 min outreach is due when the phone polls (#395)
        return await self._api(do_messages, passenger_id)

    def _after_manual(
        self, op: str, passenger_id: str, value: str, preset: str | None, out: dict[str, Any]
    ) -> None:
        if "error" in out or self.scenarios.active is None:
            return
        if preset is not None and self.scenarios.advance_if_matches(op, passenger_id, value):
            return
        if hasattr(self.runtime.llm, "record_manual"):
            self.runtime.llm.record_manual(op, passenger_id, value, self.scenarios.index)

    @shared
    async def whatsapp_reply(
        self,
        passenger_id: str,
        text: str,
        preset: str | None = None,
        step: int | None = None,
        tape: list[Any] | None = None,
    ) -> dict[str, Any]:
        from acme.api.app import do_reply

        out = await self._api(do_reply, passenger_id, text)
        self._after_manual("whatsapp_reply", passenger_id, text, preset, out)
        return out

    @shared
    async def whatsapp_action(
        self,
        passenger_id: str,
        action: str,
        preset: str | None = None,
        step: int | None = None,
        tape: list[Any] | None = None,
    ) -> dict[str, Any]:
        from acme.api.app import do_action

        out = await self._api(do_action, passenger_id, action)
        self._after_manual("whatsapp_action", passenger_id, action, preset, out)
        return out

    def register_operations(self) -> dict[str, list[str]]:
        return {
            "": [
                "diagnostics",
                "scenarios_list",
                "scenario_start",
                "scenario_state",
                "journeys_list",
                "reset",
            ],
            "stream": ["stream_query"],
            "async_stream": ["async_stream_query"],
            "async": [
                "a2a_skill",
                "scenario_step",
                "journey_detail",
                "journey_events",
                "trigger_cancellation",
                "whatsapp_messages",
                "whatsapp_reply",
                "whatsapp_action",
            ],
        }


def build_customer_engine_app() -> CustomerEngineApp:
    return CustomerEngineApp()
