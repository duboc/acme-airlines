"""Customer agent runtime: deterministic orchestration over the simulated MCP systems.

Scenario 1 (silent ingestion): subscribe to OCC `flight_ops`; for each cancellation open a
`JourneyState` per affected passenger (DETECTED -> RESOLVING), load the CRM profile and PNR,
and record the onward-connection constraint (onward departure - MCT, via `connection_check`).
Scenario 3a (`outreach`): once RESOLVED, one consolidated WhatsApp message is sent (`tick`),
and inbound passenger messages are routed (`handle_inbound`). Quiet hours: see `quiet_hours`.
Orchestration is deterministic; the LLM only words messages and parses replies (FakeLlm in CI).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel

from acme.customer import outreach
from acme.customer.change import ChangeAgent
from acme.customer.rebuild import rebuild_journey
from acme.customer.specialists.change import ReplanningChangeAgent
from acme.domain.clock import Clock, SystemClock
from acme.domain.fixtures import World
from acme.domain.journey import DisruptionStatus, JourneyState
from acme.domain.models import HotelBooking, Voucher
from acme.llm import LlmPort, build_llm
from acme.memory import JourneyStore, SqliteJourneyStore
from acme.policy.connection_check import latest_arrival
from acme.sim.client import Connector, SimClient, ToolError, inprocess_connector
from acme.sim.world import SimWorld


class WhatsAppMessage(BaseModel):
    message_id: str
    direction: Literal["inbound", "outbound"]
    text: str
    buttons: list[str] = []
    attachments: list[str] = []
    timestamp: AwareDatetime


@dataclass
class JourneyRecord:
    state: JourneyState
    passenger_id: str
    pnr_locator: str
    hotel: HotelBooking | None = None
    vouchers: list[Voucher] = field(default_factory=list)
    constraint_latest_arrival: datetime | None = None
    # Opaque per-run nonce (not derived from PNR/passenger): lets Sales tell a re-run of the same
    # (deterministic) journey id after a scenario reset from the run it already pitched (#104).
    run_id: str = field(default_factory=lambda: uuid4().hex[:12])


def _to_message(m: dict[str, Any]) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=m["message_id"],
        direction=m["direction"],
        text=m["body"],
        buttons=m["buttons"],
        attachments=m.get("attachments", []),
        timestamp=datetime.fromisoformat(m["at"]),
    )


def journey_id_for(locator: str) -> str:
    return "J-" + hashlib.sha1(locator.encode()).hexdigest()[:8]  # noqa: S324 (not security)


class CustomerRuntime:
    def __init__(
        self,
        clock: Clock | None = None,
        connector: Connector | None = None,
        connector_factory: Callable[[SimWorld], Connector] | None = None,
        auto_duty_of_care: bool = True,
        llm: LlmPort | None = None,
        notify_delay: timedelta = timedelta(minutes=6),
        change_agent: ChangeAgent | None = None,
    ) -> None:
        self.llm = llm or build_llm()
        self.change_agent: ChangeAgent = change_agent or ReplanningChangeAgent()
        self.notify_delay = notify_delay  # settle time after RESOLVED before the one notification
        self.auto_duty_of_care = auto_duty_of_care  # False isolates the rebuild in tests
        self.clock = clock or SystemClock()
        self._custom_connector = connector
        self._connector_factory = connector_factory
        self.reset()

    def reset(self) -> None:
        self.sim = SimWorld.seeded(self.clock)
        # Every path authenticates as `customer` only (acme.sim.auth, #27).
        if self._custom_connector:
            connector = self._custom_connector
        elif self._connector_factory:
            connector = self._connector_factory(self.sim)  # e.g. ADK connector to a served sim
        else:
            connector = inprocess_connector(self.sim)
        self.client = SimClient(connector)
        if hasattr(self, "store"):
            self.store.close()
        self.store: JourneyStore = SqliteJourneyStore(os.environ.get("ACME_JOURNEY_DB", ":memory:"))
        self.journeys: dict[str, JourneyRecord] = {}
        self._journey_locks: dict[str, asyncio.Lock] = {}
        self._cursor = 0
        self._lock = asyncio.Lock()

    @property
    def world(self) -> World:
        return self.sim.world

    # --- passenger messaging (WhatsApp thread) ---------------------------------------------
    @property
    def messages(self) -> dict[str, list[WhatsAppMessage]]:
        """The WhatsApp transcript per passenger, read from the whatsapp simulator."""
        return {
            pid: [_to_message(m) for m in msgs]
            for pid, msgs in self.sim.partners.transcripts.items()
        }

    def journey_lock(self, journey_id: str) -> asyncio.Lock:
        """One lock per journey: evaluate -> approve/send/invalidate is atomic (#15)."""
        return self._journey_locks.setdefault(journey_id, asyncio.Lock())

    async def send(
        self,
        passenger_id: str,
        text: str,
        attachments: Sequence[str] = (),
        buttons: Sequence[str] = (),
    ) -> WhatsAppMessage:
        sent = await self.client.call(
            "whatsapp",
            "message.send",
            to=passenger_id,
            body=text,
            attachments=list(attachments),
            buttons=list(buttons),
        )
        return _to_message(sent)

    async def handle_inbound(self, passenger_id: str, text: str) -> WhatsAppMessage:
        """A passenger message arrives (always answerable, even in quiet hours)."""
        received = await self.client.call(
            "whatsapp", "message.receive", sender=passenger_id, body=text
        )
        if rec := self.journey_for_passenger(passenger_id):
            # Transitions out of RESOLVED (change requests) take the same per-journey lock as
            # offer delivery and acceptance, so they cannot land between a check and a charge.
            async with self.journey_lock(rec.state.journey_id):
                await outreach.handle_inbound(self, rec, text)
        return _to_message(received)

    async def tick(self) -> None:
        """Run time-driven work: send each due proactive notification (once per journey).

        Check-and-send is atomic per journey (same lock as offer delivery, acceptance and
        inbound handling): concurrent ticks (console + chat polls) cannot both pass the check
        before `notification_sent` is recorded. Nothing under this lock takes it again."""
        for rec in list(self.journeys.values()):
            if not outreach.notification_due(self, rec):
                continue  # cheap pre-check without the lock
            async with self.journey_lock(rec.state.journey_id):
                if outreach.notification_due(self, rec):  # re-check inside the lock
                    await outreach.notify_resolution(self, rec)

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
            result = await rebuild_journey(self.client, rec, flight)
            if self.auto_duty_of_care:
                from acme.customer.specialists.duty import run_duty_of_care  # noqa: PLC0415

                await run_duty_of_care(self, rec, result)
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
