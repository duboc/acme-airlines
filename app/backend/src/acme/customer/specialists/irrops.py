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

    async def search(self, req: RebookRequest) -> list[dict]:
        found = await self._client.call(
            "pss", "flights.search", origin=req.origin, dest=req.destination,
            after=req.after.isoformat(),
        )  # fmt: skip
        return found["flights"]  # sorted by departure

    @staticmethod
    def feasible(req: RebookRequest, flight: dict) -> int | None:
        """Connection margin in minutes if the flight protects the onward connection (0 when
        there is none to protect), else None."""
        if req.onward_departure is None:
            return 0
        arrival = datetime.fromisoformat(flight["scheduled_arrival"])
        result = check_connection(req.destination, arrival, req.onward_departure)
        return result.margin_minutes if result.feasible else None

    async def handle(self, req: RebookRequest, emit: Emit) -> RebookResponse:
        for flight in await self.search(req):  # first feasible is the earliest
            margin = self.feasible(req, flight)
            if margin is None:
                continue
            arrival = datetime.fromisoformat(flight["scheduled_arrival"])
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
                arrival=arrival, margin_minutes=margin if req.onward_departure else None,
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
