"""Baggage specialist: route checked bags to the new flight via WorldTracer."""

from pydantic import BaseModel

from acme.customer.specialists.common import Emit
from acme.sim.client import SimClient, ToolError


class BagRequest(BaseModel):
    flight: str
    tags: list[str]


class BagResponse(BaseModel):
    rerouted: dict[str, str]  # tag -> new routing flight


class BaggageSpecialist:
    def __init__(self, client: SimClient) -> None:
        self._client = client

    async def handle(self, req: BagRequest, emit: Emit) -> BagResponse:
        rerouted = {}
        for tag in req.tags:
            try:
                res = await self._client.call(
                    "worldtracer", "bag.reroute", tag=tag, flight=req.flight
                )
            except ToolError as exc:  # partial result: callers compare against the tags
                emit(
                    "needs_human",
                    {"specialist": "baggage", "tag": tag, "reason": str(exc)[:200]},
                )
                break
            rerouted[tag] = res["routing"]
            emit("bag_rerouted", {"flight": req.flight, "tag": tag})
        return BagResponse(rerouted=rerouted)
