"""Sales agent packaged as the Agent Engine app `acme-sales-agent` (#161).

The radar and `SalesAgent` are unchanged. The only outbound path to the Customer side is
`EngineCustomerClient` (the Customer engine's `a2a_skill`: three PII-free skills); inventory
comes from an in-process simulator authenticated as `sales` only. No network calls of its own.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from acme.diagnostics import format_error
from acme.engines.customer import (
    EngineConfigError,
    EngineOperationError,
    engine_extra_packages,
    engine_requirements,
    validate_env,
)
from acme.sales.client import CustomerEngineHandle, build_customer_client
from acme.telemetry import EngineOp, flush

DISPLAY_NAME = "acme-sales-agent"
APP_NAME = "acme-sales"

__all__ = ["DISPLAY_NAME", "EngineConfigError", "engine_extra_packages", "engine_requirements"]


class SalesEngineApp:
    _STARTED = ("radar", "_runner", "_sessions")

    def __init__(self, customer_engine: CustomerEngineHandle | None = None) -> None:
        self.llm_mode = validate_env()
        self._customer_engine = customer_engine  # tests inject a fake handle
        self.startup_error: str | None = None
        self.started = False
        self.shared: Any = None  # SharedState when ACME_STATE_DB is set (built in set_up)

    def __getattr__(self, name: str) -> Any:
        if name in SalesEngineApp._STARTED:
            if self.startup_error:
                raise EngineOperationError(
                    "the engine failed to start; call `diagnostics` for the error"
                )
            raise EngineOperationError("the engine is not started yet (set_up() has not run)")
        raise AttributeError(name)

    def set_up(self) -> None:
        """Failures are captured (scrubbed) and exposed by `diagnostics`."""
        try:
            self._set_up()
            self.started, self.startup_error = True, None
        except Exception as exc:  # noqa: BLE001 - reported by deploy.verify_started
            self.started, self.startup_error = False, format_error(exc)
            logging.getLogger(__name__).error("engine start-up failed: %s", self.startup_error)

    def diagnostics(self) -> dict[str, Any]:
        status = "ready" if self.started else ("failed" if self.startup_error else "starting")
        return {
            "engine": DISPLAY_NAME,
            "status": status,
            "startup_error": self.startup_error,
            "llm_mode": self.llm_mode,
        }

    def _set_up(self) -> None:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from acme.logging_setup import configure_logging
        from acme.metrics import configure_cloud_monitoring
        from acme.sales.agent import build_sales_agent
        from acme.sales.radar import SalesRadar
        from acme.sim.client import SimClient, inprocess_connector
        from acme.sim.world import SimWorld
        from acme.telemetry import configure_cloud_trace

        configure_logging()  # no passenger PII in Cloud Logging (#170)
        configure_cloud_trace(DISPLAY_NAME)
        configure_cloud_monitoring(DISPLAY_NAME)
        self.llm_mode = validate_env()  # refresh in the unpickled container environment (#440)
        env = {"ENGINE_MODE": "agent-engine", **self._env()}
        customer = build_customer_client(env, engine=self._customer_engine)
        sim = SimWorld.seeded()
        self.radar = SalesRadar(customer, SimClient(inprocess_connector(sim, "sales")))
        self._sessions = InMemorySessionService()
        self._runner = Runner(
            app_name=APP_NAME, agent=build_sales_agent(self.radar), session_service=self._sessions
        )
        if state_db := os.environ.get("ACME_STATE_DB"):
            from acme.state.shared import SharedState

            self.shared = SharedState(state_db)

    @staticmethod
    def _env() -> dict[str, str]:
        import os

        return {"ACME_CUSTOMER_ENGINE": os.environ.get("ACME_CUSTOMER_ENGINE", "")}

    async def async_stream_query(
        self, *, message: str, user_id: str, session_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        from google.genai import types

        sid = session_id or f"s-{user_id}"
        # The radar's sent-set is the only Sales state: share it across processes (#200). The
        # lock covers the whole tick, so two processes never both propose for the same journey.
        op = EngineOp("sales_tick")
        try:
            with op.span():
                async with self._exclusive():
                    if self.shared is not None and (blob := self.shared.get_blob("sales_sent")):
                        self.radar.import_sent(json.loads(blob))
                    try:
                        got = await self._sessions.get_session(
                            app_name=APP_NAME, user_id=user_id, session_id=sid
                        )
                        if got is None:
                            await self._sessions.create_session(
                                app_name=APP_NAME, user_id=user_id, session_id=sid
                            )
                        content = types.Content(role="user", parts=[types.Part(text=message)])
                        async for event in self._runner.run_async(
                            user_id=user_id, session_id=sid, new_message=content
                        ):
                            yield event.model_dump(mode="json", exclude_none=True)
                    finally:
                        if self.shared is not None:
                            self.shared.set_blob("sales_sent", json.dumps(self.radar.export_sent()))
        finally:
            op.log(flush())  # export the pitch and A2A spans before the process is throttled

    def _exclusive(self):
        return contextlib.nullcontext() if self.shared is None else self.shared.exclusive()

    def register_operations(self) -> dict[str, list[str]]:
        return {"": ["diagnostics"], "async_stream": ["async_stream_query"]}


def build_sales_engine_app(customer_engine: CustomerEngineHandle | None = None) -> SalesEngineApp:
    return SalesEngineApp(customer_engine)
