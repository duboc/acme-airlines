"""A2A client for the Customer agent's three skills (the Sales agent's only way to reach it)."""

from __future__ import annotations

from itertools import count
from typing import Any, Protocol

import httpx

from acme.domain.models import Offer


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
