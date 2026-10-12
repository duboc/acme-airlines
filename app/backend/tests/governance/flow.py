"""Full-flow harness for the outcome and race tests (real runtime, A2A wire format, FakeLlm).

Drives Mariana's journey with the real Customer runtime, the real Sales radar (over the A2A
JSON-RPC wire, via an in-process transport) and a spying FakeLlm. Time is a FixedClock.
`interleave` injects a passenger/system action at an exact await point (a tool call) to
reproduce time-of-check/time-of-use races deterministically.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

import httpx

from acme.customer.runtime import CustomerRuntime
from acme.customer.skills import CustomerSkills
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT, GRU
from acme.domain.journey import DisruptionStatus as S
from acme.engines.customer import build_customer_engine_app
from acme.llm import FakeLlm
from acme.sales.client import A2ACustomerClient, EngineCustomerClient
from acme.sales.radar import SalesRadar
from acme.sim.client import SimClient, inprocess_connector
from acme.sim.dcs import dcs_state

PID = "P-MARIANA"
PNR = "MSLV42"
NOTIFY_AT = datetime(2026, 10, 9, 22, 9, tzinfo=GRU)


class SpyLlm(FakeLlm):
    """Records every input the LLM ever sees (composition contexts and parsed passenger text)."""

    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[str] = []

    async def compose_passenger_message(self, context):
        self.inputs.append(json.dumps(context, default=str))
        return await super().compose_passenger_message(context)

    async def parse_passenger_request(self, text):
        self.inputs.append(text)
        return await super().parse_passenger_request(text)


def offer_payload(jid: str, **over) -> dict:
    base = dict(
        offer_id=str(uuid4()), journey_id=jid, flight="AC1252", cabin="premium_business",
        price="135.00", standard_price="290.00", net_margin="80.00", currency="USD",
        origin="sales", optional=True,
    )  # fmt: skip
    return {**base, **over}


def engine_over(rt: CustomerRuntime):
    """A local Customer engine app on `rt`, used as the (fake, no-GCP) engine handle."""
    app = build_customer_engine_app()
    app.set_up()
    app.runtime, app.skills = rt, CustomerSkills(rt)
    return app


class Flow:
    def __init__(self, transport: str = "http") -> None:
        self.llm = SpyLlm()
        self.rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=self.llm)
        self.skills = CustomerSkills(self.rt)

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=await self.skills.handle_a2a(json.loads(request.content))
            )

        self.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://customer"
        )
        customer = (
            EngineCustomerClient(engine_over(self.rt))
            if transport == "engine"
            else A2ACustomerClient(self.http)
        )
        self.radar = SalesRadar(customer, SimClient(inprocess_connector(self.rt.sim, "sales")))
        self.jid = ""

    # --- the story, one step at a time; the Sales radar ticks after every step ---------------
    async def cancel(self) -> None:
        (self.jid,) = await self.rt.trigger_cancellation("AC1234", "unscheduled maintenance")
        await self.radar.tick()

    async def notify(self) -> None:
        self.rt.clock.advance(NOTIFY_AT - self.rt.clock.now())
        await self.rt.tick()
        await self.radar.tick()

    async def say(self, text: str) -> None:
        await self.rt.handle_inbound(PID, text)
        await self.radar.tick()

    async def acknowledged_replan(self) -> None:
        await self.cancel()
        await self.notify()
        await self.say("Can I leave after 10am?")
        await self.say("Perfect, thanks!")

    # --- observations -------------------------------------------------------------------------
    @property
    def rec(self):
        return self.rt.journeys[self.jid]

    @property
    def state(self):
        return self.rec.state

    def outbound(self) -> list[dict]:
        return [
            m for m in self.rt.sim.partners.transcripts.get(PID, []) if m["direction"] == "outbound"
        ]

    def offer_messages(self) -> list[dict]:
        return [m for m in self.outbound() if m["buttons"]]

    def events(self, type_: str):
        return [e for e in self.state.timeline if e.type == type_]

    def charges(self):
        return self.rt.sim.partners.charges

    def seat(self) -> str | None:
        seat = next((s for s in self.rt.world.pnrs[PNR].seats if s.flight_number == "AC1252"), None)
        return seat.seat if seat else None

    async def sent_offer_ready(self) -> dict:
        """Journey RESOLVED on AC1252, acknowledged, one offer delivered via the radar."""
        await self.acknowledged_replan()
        assert len(self.offer_messages()) == 1
        return self.state.offers[0].offer

    # --- race injection -----------------------------------------------------------------------
    def interleave(self, server: str, tool: str, action: Callable[[], object]) -> None:
        """Run `action` right before the first `server/tool` call (an await point)."""
        real, fired = self.rt.client.call, []

        async def hooked(srv, name, **args):
            if (srv, name) == (server, tool) and not fired:
                fired.append(True)
                result = action()
                if inspect.isawaitable(result):
                    await result
            return await real(srv, name, **args)

        self.rt.client.call = hooked  # type: ignore[method-assign]

    def to_replanning(self) -> None:
        self.state.transition(S.REPLANNING)

    def other_pnr_takes(self, seat: str = "2A") -> None:
        from acme.domain.models import PNR as Pnr
        from acme.domain.models import Segment

        self.rt.world.pnrs["OTHER1"] = Pnr(
            locator="OTHER1", passenger_id=PID, segments=[Segment(flight_number="AC1252")]
        )
        dcs_state(self.rt.sim).hold("AC1252", seat, "OTHER1")
