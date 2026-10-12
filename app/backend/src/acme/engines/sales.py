"""Sales agent packaged as the Agent Engine app `acme-sales-agent` (#161).

The radar and `SalesAgent` are unchanged. The only outbound path to the Customer side is
`EngineCustomerClient` (the Customer engine's `a2a_skill`: three PII-free skills); inventory
comes from an in-process simulator authenticated as `sales` only. No network calls of its own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from acme.engines.customer import (
    EngineConfigError,
    engine_extra_packages,
    engine_requirements,
    validate_env,
)
from acme.sales.client import CustomerEngineHandle, build_customer_client

DISPLAY_NAME = "acme-sales-agent"
APP_NAME = "acme-sales"

__all__ = ["DISPLAY_NAME", "EngineConfigError", "engine_extra_packages", "engine_requirements"]


class SalesEngineApp:
    def __init__(self, customer_engine: CustomerEngineHandle | None = None) -> None:
        validate_env()
        self._customer_engine = customer_engine  # tests inject a fake handle

    def set_up(self) -> None:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from acme.logging_setup import configure_logging
        from acme.sales.agent import build_sales_agent
        from acme.sales.radar import SalesRadar
        from acme.sim.client import SimClient, inprocess_connector
        from acme.sim.world import SimWorld

        configure_logging()  # no passenger PII in Cloud Logging (#170)
        env = {"ENGINE_MODE": "agent-engine", **self._env()}
        customer = build_customer_client(env, engine=self._customer_engine)
        sim = SimWorld.seeded()
        self.radar = SalesRadar(customer, SimClient(inprocess_connector(sim, "sales")))
        self._sessions = InMemorySessionService()
        self._runner = Runner(
            app_name=APP_NAME, agent=build_sales_agent(self.radar), session_service=self._sessions
        )

    @staticmethod
    def _env() -> dict[str, str]:
        import os

        return {"ACME_CUSTOMER_ENGINE": os.environ.get("ACME_CUSTOMER_ENGINE", "")}

    async def async_stream_query(
        self, *, message: str, user_id: str, session_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        from google.genai import types

        sid = session_id or f"s-{user_id}"
        got = await self._sessions.get_session(app_name=APP_NAME, user_id=user_id, session_id=sid)
        if got is None:
            await self._sessions.create_session(app_name=APP_NAME, user_id=user_id, session_id=sid)
        content = types.Content(role="user", parts=[types.Part(text=message)])
        async for event in self._runner.run_async(
            user_id=user_id, session_id=sid, new_message=content
        ):
            yield event.model_dump(mode="json", exclude_none=True)

    def register_operations(self) -> dict[str, list[str]]:
        return {"async_stream": ["async_stream_query"]}


def build_sales_engine_app(customer_engine: CustomerEngineHandle | None = None) -> SalesEngineApp:
    return SalesEngineApp(customer_engine)
