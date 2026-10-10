"""Sales agent as an ADK custom `BaseAgent` (deterministic, no model call) plus its A2A card."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from a2a.types import AgentCapabilities, AgentCard
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types
from pydantic import ConfigDict

AGENT_NAME = "sales_agent"
DESCRIPTION = "ACME Sales agent: margin radar. Proposes offers to the Customer agent only."


class SalesAgent(BaseAgent):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    radar: Any  # SalesRadar

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        offers = await self.radar.tick()
        text = f"proposed {len(offers)} offer(s)" if offers else "no offers proposed"
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=text)]),
        )


def build_sales_agent(radar: Any) -> SalesAgent:
    return SalesAgent(name=AGENT_NAME, description=DESCRIPTION, radar=radar)


def sales_agent_card(url: str) -> AgentCard:
    return AgentCard(
        name=AGENT_NAME,
        description=DESCRIPTION,
        url=url,
        version="0.1.0",
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],  # Sales exposes nothing inbound: it only calls the Customer agent
    )
