"""Customer agent as an ADK custom `BaseAgent` (deterministic; no `LlmAgent`, no model call).

A thin wrapper (ADR 0100): `CustomerRuntime` keeps all behaviour; the agent just ingests new
OCC events when run and reports the journeys opened. The A2A card has no skills yet (#14).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from a2a.types import AgentCapabilities, AgentCard
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types
from pydantic import ConfigDict

from acme.customer.runtime import CustomerRuntime

AGENT_NAME = "customer_agent"
CARD_PATH = "/.well-known/agent-card.json"


class CustomerAgent(BaseAgent):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    runtime: Any  # CustomerRuntime (typed Any to keep pydantic from validating the dataclass tree)

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        opened = await self.runtime.poll(timeout_s=0)
        text = "opened journeys: " + ", ".join(opened) if opened else "no new disruptions"
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=text)]),
        )


def build_customer_agent(runtime: CustomerRuntime) -> CustomerAgent:
    return CustomerAgent(
        name=AGENT_NAME,
        description="ACME Customer agent: owns the passenger journey during disruptions.",
        runtime=runtime,
    )


def customer_agent_card(url: str) -> AgentCard:
    return AgentCard(
        name=AGENT_NAME,
        description="ACME Customer agent: owns the passenger journey during disruptions.",
        url=url,
        version="0.1.0",
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],  # #14 adds list_journeys, journey_status, propose_offer
    )
