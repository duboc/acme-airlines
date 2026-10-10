"""Customer agent runtime: deterministic orchestration over the simulated MCP systems.

Scenario 1 (silent ingestion): subscribe to OCC `flight_ops`; for each cancellation open a
`JourneyState` per affected passenger (DETECTED -> RESOLVING), load the CRM profile and PNR,
and record the onward-connection constraint (onward departure - MCT, via `connection_check`).
Nothing is sent to the passenger here; see `quiet_hours`. No LLM is used.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel

from acme.domain.clock import Clock, SystemClock
from acme.domain.fixtures import World
from acme.domain.journey import DisruptionStatus, JourneyState
from acme.domain.models import HotelBooking, Voucher
from acme.policy.connection_check import latest_arrival
from acme.sim.client import Connector, SimClient, ToolError, inprocess_connector
from acme.sim.world import SimWorld


class WhatsAppMessage(BaseModel):
    message_id: str
    direction: Literal["inbound", "outbound"]
    text: str
    buttons: list[str] = []
    timestamp: AwareDatetime


@dataclass
class JourneyRecord:
    state: JourneyState
    passenger_id: str
    pnr_locator: str
    hotel: HotelBooking | None = None
    vouchers: list[Voucher] = field(default_factory=list)
    constraint_latest_arrival: datetime | None = None


def journey_id_for(locator: str) -> str:
    return "J-" + hashlib.sha1(locator.encode()).hexdigest()[:8]  # noqa: S324 (not security)


class CustomerRuntime:
    def __init__(self, clock: Clock | None = None, connector: Connector | None = None) -> None:
        self.clock = clock or SystemClock()
        self._custom_connector = connector
        self.reset()

    def reset(self) -> None:
        self.sim = SimWorld.seeded(self.clock)
        # Plain config for now; #27 swaps in a per-caller credentialed connector.
        self.client = SimClient(self._custom_connector or inprocess_connector(self.sim))
        self.journeys: dict[str, JourneyRecord] = {}
        self.messages: dict[str, list[WhatsAppMessage]] = {}
        self._cursor = 0
        self._lock = asyncio.Lock()

    @property
    def world(self) -> World:
        return self.sim.world

    # --- passenger messaging (WhatsApp thread) ---------------------------------------------
    def say(self, passenger_id: str, direction: str, text: str, buttons=()) -> WhatsAppMessage:
        msg = WhatsAppMessage(
            message_id=str(uuid4()),
            direction=direction,
            text=text,
            buttons=list(buttons),
            timestamp=self.clock.now(),
        )
        self.messages.setdefault(passenger_id, []).append(msg)
        return msg

    def journey_for_passenger(self, passenger_id: str) -> JourneyRecord | None:
        for rec in self.journeys.values():
            if rec.passenger_id == passenger_id:
                return rec
        return None

    # --- scenario 1 ---------------------------------------------------------------------------
    async def trigger_cancellation(self, flight: str, reason: str) -> list[str]:
        """OCC publishes a cancellation; the agent then ingests it. Raises KeyError."""
        try:
            await self.client.call(
                "occ", "flight_ops.publish_cancellation", flight=flight, reason=reason
            )
        except ToolError as exc:
            raise KeyError(flight) from exc
        return await self.poll(timeout_s=0)

    async def poll(self, timeout_s: float = 0) -> list[str]:
        """Ingest new OCC events; returns the ids of journeys opened by this call."""
        async with self._lock:
            res = await self.client.call(
                "occ", "flight_ops.subscribe", cursor=self._cursor, timeout_s=timeout_s
            )
            self._cursor = res["next_cursor"]
            opened: list[str] = []
            for event in res["events"]:
                if event["type"] == "flight_cancelled":
                    opened += await self._on_cancellation(event["flight"], event["reason"])
            return opened

    async def _on_cancellation(self, flight: str, reason: str) -> list[str]:
        manifest = await self.client.call("pss", "flight.manifest", flight=flight)
        opened = []
        for entry in manifest["pnrs"]:
            jid = journey_id_for(entry["pnr"])
            if jid in self.journeys:
                continue
            state = JourneyState(jid, self.clock)
            rec = JourneyRecord(state, entry["passenger_id"], entry["pnr"])
            self.journeys[jid] = rec
            state.append_event("occ", "cancellation_detected", {"flight": flight, "reason": reason})
            state.transition(DisruptionStatus.RESOLVING, actor="customer_agent")
            await self._load_context(rec, flight)
            opened.append(jid)
        return opened

    async def _load_context(self, rec: JourneyRecord, flight: str) -> None:
        state = rec.state
        profile = await self.client.call("crm", "profile.get", passenger_id=rec.passenger_id)
        pnr = await self.client.call("pss", "pnr.get", pnr=rec.pnr_locator)
        seat = next((s["seat"] for s in pnr["seats"] if s["flight_number"] == flight), None)
        flights = [s["flight_number"] for s in pnr["segments"]]
        onward = None
        if flight in flights and flights.index(flight) + 1 < len(flights):
            onward = await self.client.call(
                "pss", "flights.get", flight=flights[flights.index(flight) + 1]
            )
        connection = None
        if onward:
            connection = {"flight": onward["number"], "departure": onward["scheduled_departure"]}
        state.append_event(
            "customer_agent",
            "profile_loaded",
            {
                "tier": profile["tier"],
                "seat": seat,
                "bags": len(pnr["bags"]),
                "connection": connection,
            },
        )
        if onward:
            await self._record_constraint(rec, onward)

    async def _record_constraint(self, rec: JourneyRecord, onward: dict[str, Any]) -> None:
        departure = datetime.fromisoformat(onward["scheduled_departure"])
        airport = onward["origin"]
        latest, mct = latest_arrival(airport, departure)
        rec.constraint_latest_arrival = latest
        local = latest.strftime("%Y-%m-%d %H:%M")
        rec.state.append_event(
            "customer_agent",
            "connection_constraint",
            {
                "airport": airport,
                "mct_minutes": mct,
                "latest_arrival": latest.isoformat(),
                "text": f"must arrive {airport} by {local}",
            },
        )
