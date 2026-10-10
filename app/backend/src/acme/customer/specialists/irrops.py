"""IRROPS Rebooking specialist: earliest flight that still protects the onward connection."""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel

from acme.customer.specialists.common import Emit
from acme.policy.connection_check import check_connection
from acme.sim.client import SimClient


class RebookRequest(BaseModel):
    pnr: str
    origin: str
    destination: str
    after: AwareDatetime  # earliest acceptable departure (the cancelled flight's schedule)
    onward_departure: AwareDatetime | None = None  # departure of the onward connection, if any


class RebookResponse(BaseModel):
    status: Literal["rebooked", "no_feasible_flight"]
    flight: str | None = None
    departure: AwareDatetime | None = None
    arrival: AwareDatetime | None = None
    margin_minutes: int | None = None


class IrropsRebooking:
    def __init__(self, client: SimClient) -> None:
        self._client = client

    async def handle(self, req: RebookRequest, emit: Emit) -> RebookResponse:
        found = await self._client.call(
            "pss", "flights.search", origin=req.origin, dest=req.destination,
            after=req.after.isoformat(),
        )  # fmt: skip
        for flight in found["flights"]:  # sorted by departure: first feasible is the earliest
            arrival = datetime.fromisoformat(flight["scheduled_arrival"])
            margin = None
            if req.onward_departure is not None:
                result = check_connection(req.destination, arrival, req.onward_departure)
                if not result.feasible:
                    continue
                margin = result.margin_minutes
            await self._client.call("pss", "pnr.rebook", pnr=req.pnr, flight=flight["number"])
            departure = datetime.fromisoformat(flight["scheduled_departure"])
            emit(
                "rebooked",
                {
                    "flight": flight["number"],
                    "departure": departure.isoformat(),
                    "arrival": arrival.isoformat(),
                },
            )
            return RebookResponse(
                status="rebooked", flight=flight["number"], departure=departure,
                arrival=arrival, margin_minutes=margin,
            )  # fmt: skip
        emit(
            "needs_human",
            {
                "specialist": "irrops_rebooking",
                "reason": f"no feasible {req.origin}-{req.destination} flight protects the "
                "onward connection",
            },
        )
        return RebookResponse(status="no_feasible_flight")
