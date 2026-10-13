"""A2A client for the Customer agent's three skills (the Sales agent's only way to reach it)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from itertools import count
from typing import Any, Protocol

import httpx
from opentelemetry.trace import SpanKind

from acme.domain.models import Offer
from acme.telemetry import span


class CustomerPort(Protocol):
    async def list_journeys(self) -> list[str]: ...
    async def journey_status(self, journey_id: str) -> dict[str, Any]: ...
    async def propose_offer(self, offer: Offer) -> dict[str, Any]: ...


class A2AError(RuntimeError):
    pass


class A2ACustomerClient:
    """JSON-RPC `message/send` with exactly one data part `{skill, args}`; never free text."""

    def __init__(self, http: httpx.AsyncClient, path: str = "/a2a") -> None:
        self._http, self._path, self._ids = http, path, count(1)

    async def _skill(self, skill: str, args: dict[str, Any]) -> dict[str, Any]:
        body = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": f"sales-{next(self._ids)}",
                    "role": "agent",
                    "parts": [{"kind": "data", "data": {"skill": skill, "args": args}}],
                }
            },
        }
        with span(f"acme.a2a.{skill}", kind=SpanKind.CLIENT, **_span_attrs(skill, args)):
            resp = (await self._http.post(self._path, json=body)).json()
        if "error" in resp:
            raise A2AError(resp["error"]["message"])
        return resp["result"]["parts"][0]["data"]

    async def list_journeys(self) -> list[str]:
        return (await self._skill("list_journeys", {}))["journey_ids"]

    async def journey_status(self, journey_id: str) -> dict[str, Any]:
        return await self._skill("journey_status", {"journey_id": journey_id})

    async def propose_offer(self, offer: Offer) -> dict[str, Any]:
        return await self._skill("propose_offer", {"offer": offer.model_dump(mode="json")})


def _span_attrs(skill: str, args: dict[str, Any]) -> dict[str, Any]:
    """Skill and journey id only (never the offer or any text)."""
    journey = args.get("journey_id") or (args.get("offer") or {}).get("journey_id")
    return {
        "acme__skill": skill,
        **({"acme__journey_id": journey} if isinstance(journey, str) else {}),
    }


class CustomerEngineHandle(Protocol):
    """A handle on the Customer engine: only its `a2a_skill` operation is ever used."""

    async def a2a_skill(self, skill: str, args: dict[str, Any]) -> dict[str, Any]: ...


class EngineCustomerClient:
    """`CustomerPort` over the Customer engine's `a2a_skill` operation.

    Exactly the three skills, no generic "call any skill" method. The Customer side enforces the
    allowlist and the structured-offer schema; this client only unwraps the reply.
    """

    def __init__(self, engine: CustomerEngineHandle) -> None:
        self._engine = engine

    async def _skill(self, skill: str, args: dict[str, Any]) -> dict[str, Any]:
        # A client span in the Sales trace: the Customer engine's own span lives in another
        # process/trace (no context crosses `a2a_skill`), so this is the span that sits next to
        # `acme.sales.pitch`; both carry the skill and journey id for correlation (#215).
        with span(f"acme.a2a.{skill}", kind=SpanKind.CLIENT, **_span_attrs(skill, args)):
            reply = await self._engine.a2a_skill(skill, args)
        if "error" in reply:  # the JSON-RPC error body, identical to HTTP /a2a (#175)
            raise A2AError(reply["error"]["message"])
        return reply

    async def list_journeys(self) -> list[str]:
        return (await self._skill("list_journeys", {}))["journey_ids"]

    async def journey_status(self, journey_id: str) -> dict[str, Any]:
        return await self._skill("journey_status", {"journey_id": journey_id})

    async def propose_offer(self, offer: Offer) -> dict[str, Any]:
        return await self._skill("propose_offer", {"offer": offer.model_dump(mode="json")})


class _AdcEngineHandle:
    """Real handle: the deployed Customer engine, queried with ADC (never used in CI)."""

    def __init__(self, resource_name: str) -> None:
        import vertexai
        from vertexai import agent_engines

        # Engine resources live in a regional location; model calls elsewhere stay on `global`.
        _, project, _, location, *_ = resource_name.split("/")
        vertexai.init(project=project, location=location)
        self._engine = agent_engines.get(resource_name)

    async def a2a_skill(self, skill: str, args: dict[str, Any]) -> dict[str, Any]:
        return await self._engine.a2a_skill(skill=skill, args=args)


def build_customer_client(
    env: Mapping[str, str] | None = None,
    http: httpx.AsyncClient | None = None,
    engine: CustomerEngineHandle | None = None,
) -> CustomerPort:
    """`ENGINE_MODE=local` (default): A2A over HTTP. `agent-engine`: the Customer engine."""
    env = os.environ if env is None else env
    mode = env.get("ENGINE_MODE") or "local"
    if mode == "local":
        if http is None:
            raise ValueError("local mode needs an httpx client for the Customer A2A endpoint")
        return A2ACustomerClient(http)
    if mode != "agent-engine":
        raise ValueError(f"ENGINE_MODE must be 'local' or 'agent-engine', got {mode!r}")
    if engine is None:
        resource = env.get("ACME_CUSTOMER_ENGINE", "")
        if not resource.startswith("projects/") or "/reasoningEngines/" not in resource:
            raise ValueError("ACME_CUSTOMER_ENGINE must be the full engine resource name")
        engine = _AdcEngineHandle(resource)
    return EngineCustomerClient(engine)
