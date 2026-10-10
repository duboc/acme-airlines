"""Customer agent as an ADK custom `BaseAgent` (deterministic; no `LlmAgent`, no model call).

A thin wrapper (ADR 0100): `CustomerRuntime` keeps all behaviour; the agent just ingests new
OCC events when run and reports the journeys opened. The A2A card lists the three
Sales-facing skills (`acme.customer.skills`).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types
from pydantic import ConfigDict

from acme.customer.runtime import CustomerRuntime

AGENT_NAME = "customer_agent"
CARD_PATH = "/.well-known/agent-card.json"


SKILL_CARDS = [
    AgentSkill(
        id="list_journeys", name="List open journeys", tags=["sales"],
        description="Open journey ids. No passenger data.",
    ),
    AgentSkill(
        id="journey_status", name="Journey status", tags=["sales"],
        description="{status, flight, resolution_acknowledged} only. No passenger data.",
    ),
    AgentSkill(
        id="propose_offer", name="Propose offer", tags=["sales"],
        description="Accepts only a structured Offer (no free text); recorded as proposed.",
    ),
]  # fmt: skip


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
        skills=SKILL_CARDS,
    )
