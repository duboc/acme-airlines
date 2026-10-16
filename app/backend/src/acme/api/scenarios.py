"""Demo scenario presets (#145): scripted, deterministic variants of Mariana's AC1234 story.

A preset resets the world (fixed clock, FakeLlm), applies a seed variant, then each `step`
performs the next scripted action (OCC cancellation, time passing, a passenger reply or tap) and
lets the Sales radar tick, exactly like the governance `Flow` harness. Fake mode only: with
`LLM_MODE=vertex` the endpoints answer 404, because presets are a demo affordance.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

import httpx
from pydantic import BaseModel

from acme.customer.conversion import accept_offer, decline_offer
from acme.customer.runtime import CustomerRuntime, JourneyRecord
from acme.customer.skills import CustomerSkills
from acme.domain.clock import FixedClock, SystemClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT, GRU
from acme.sales.client import A2ACustomerClient
from acme.sales.radar import SalesRadar
from acme.sim.client import SimClient, inprocess_connector

PID, PNR = "P-MARIANA", "MSLV42"
NOTIFY_AT = datetime(2026, 10, 9, 22, 9, tzinfo=GRU)
Kind = Literal["cancel", "notify", "reply", "accept", "decline"]


@dataclass(frozen=True)
class Step:
    kind: Kind
    label: str
    text: str = ""


@dataclass(frozen=True)
class Preset:
    id: str
    title: str
    description: str
    expectation: str
    steps: tuple[Step, ...]
    seed: Callable[[CustomerRuntime], None] | None = None


class PresetView(BaseModel):
    id: str
    title: str
    description: str
    steps: list[str]
    expectation: str


class ScenarioState(BaseModel):
    scenario_id: str
    step_index: int  # steps performed so far
    total_steps: int
    done: bool
    last_step: str | None
    next_step: str | None
    journey_id: str | None
    journey_status: str | None
    flight: str | None
    seat: str | None
    seat_reason: str | None
    offers_sent: int
    offer_status: str | None
    charged: str
    needs_human: bool
    last_message: str | None


CANCEL = Step("cancel", "OCC cancels AC1234 (unscheduled maintenance)")
NOTIFY = Step("notify", "Time passes: Mariana gets the 22:09 resolution message")
AFTER_10 = Step("reply", "Mariana: 'Can I leave after 10am?'", "Can I leave after 10am?")
ACK = Step("reply", "Mariana: 'Perfect, thanks!'", "Perfect, thanks!")
ACCEPT = Step("accept", "Mariana taps Accept on the upgrade")
UPTO_OFFER = (CANCEL, NOTIFY, AFTER_10, ACK)


def _seat_taken(rt: CustomerRuntime) -> None:
    ac1240 = rt.world.flights["AC1240"]
    ac1240.open_seats = {"economy": ["15A", "20F"]}  # 14A is gone


def _no_hotel(rt: CustomerRuntime) -> None:
    rt.world.hotels_with_rooms = []


PRESETS: tuple[Preset, ...] = (
    Preset(
        "happy-path", "Happy path",
        "Scenarios 1-5 end to end: rebooked, replanned after 10am, acknowledged, upgrade accepted.",
        "Journey CLOSED on AC1252, seat 2A, $135.00 charged once.",
        (*UPTO_OFFER, ACCEPT),
    ),
    Preset(
        "declines-upgrade", "Declines the upgrade",
        "Mariana answers 'No thanks' to the optional upgrade.",
        "Keeps seat 14A on AC1252, nothing charged, and no further offer is ever made.",
        (*UPTO_OFFER, Step("decline", "Mariana taps 'No thanks'")),
    ),
    Preset(
        "unsatisfiable-request", "Unsatisfiable request",
        "Mariana asks to leave after 1pm, which would miss her Santiago connection.",
        "An honest message; she stays on AC1240; no offer.",
        (CANCEL, NOTIFY,
         Step("reply", "Mariana: 'Can I leave after 1pm?'", "Can I leave after 1pm?")),
    ),
    Preset(
        "seat-taken", "Seat 14A taken",
        "14A is no longer open on the replacement flight AC1240.",
        "Nearest window seat 15A, with the reason recorded.",
        (CANCEL, NOTIFY), seed=_seat_taken,
    ),
    Preset(
        "hotel-unavailable", "Hotel unavailable",
        "The partner hotel has no rooms.",
        "needs_human, never RESOLVED, no offer and no charge.",
        (CANCEL, NOTIFY), seed=_no_hotel,
    ),
    Preset(
        "upgrade-race", "Upgrade race",
        "Mariana asks to replan after 12pm, then taps the now-stale Accept button (#88).",
        "The offer is invalidated, nothing is charged and she keeps seat 14A.",
        (*UPTO_OFFER,
         Step("reply", "Mariana: 'Can I leave after 12pm?'", "Can I leave after 12pm?"),
         Step("accept", "Mariana taps the stale Accept button")),
    ),
)  # fmt: skip
BY_ID = {p.id: p for p in PRESETS}


def preset_view(p: Preset) -> PresetView:
    return PresetView(
        id=p.id, title=p.title, description=p.description, expectation=p.expectation,
        steps=[s.label for s in p.steps],
    )  # fmt: skip


_PASSENGER_FACING = ("sent", "accepted", "declined", "invalidated")


def passenger_offer_status(offers: list) -> str | None:
    """Status of the offer the passenger saw. A rejected duplicate proposal (e.g. from the
    background Sales radar) is recorded after it and must not mask it."""
    shown = [r for r in offers if r.status in _PASSENGER_FACING]
    chosen = shown or offers
    return chosen[-1].status if chosen else None


def matches_step(step: Step, op: str, passenger_id: str, value: str) -> bool:
    """Whether a manual WhatsApp reply or action matches a scripted preset step (#322)."""
    if passenger_id != PID:
        return False
    if op == "whatsapp_reply":
        return step.kind == "reply" and step.text == value
    if op == "whatsapp_action":
        return (step.kind == "accept" and value == "accept_offer") or (
            step.kind == "decline" and value == "decline_offer"
        )
    return False


class ScenarioRunner:
    def __init__(self, rt: CustomerRuntime) -> None:
        self.rt = rt
        self.active: Preset | None = None
        self.index = 0
        self.radar: SalesRadar | None = None
        self._radar_sim: object | None = None  # the world `radar` reads

    def _radar_for_world(self) -> SalesRadar:
        """The Sales radar of the CURRENT world; kept while the world is (its sent-set with it)."""
        if self.radar is None or self._radar_sim is not self.rt.sim:
            skills = CustomerSkills(self.rt)

            async def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200, json=await skills.handle_a2a(json.loads(request.content))
                )

            http = httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://customer"
            )
            self.radar = SalesRadar(
                A2ACustomerClient(http), SimClient(inprocess_connector(self.rt.sim, "sales"))
            )
            self._radar_sim = self.rt.sim
        return self.radar

    async def tick_sales(self) -> None:
        """One Sales radar pass, preset or not (a manual reply is acknowledged either way)."""
        await self._radar_for_world().tick()

    def end_preset(self, flight: str) -> None:
        """A manual cancellation ends the preset. The primary flight (or no journey yet) is a
        fresh start: the full reset of #321. A secondary one acts on the journey the preset left
        open (#444): drop the cursor and the fixed clock, keep the world and its journeys (#485)."""
        if self.active is None:
            return
        if flight == "AC1234" or not self.rt.journeys:
            self.reset()
            return
        self.active, self.index = None, 0
        self.rt.clock = self.rt._initial_clock or SystemClock()  # noqa: SLF001
        self.rt.sim.clock = self.rt.clock
        for rec in self.rt.journeys.values():
            rec.state.set_clock(self.rt.clock)

    def reset(self) -> None:
        """Back to no preset, the initial clock and an empty world (#321)."""
        self.active, self.index = None, 0
        self.rt.reset()

    def start(self, preset: Preset) -> ScenarioState:
        self.rt.reset(FixedClock(DISRUPTION_DETECTED_AT))
        self.active, self.index = preset, 0
        if preset.seed:
            preset.seed(self.rt)
        self._radar_for_world()
        return self.state()

    async def step(self, sales_tick: bool = True) -> ScenarioState:
        """Run the next scripted action. The engine passes False: the demo drives Sales."""
        assert self.active is not None
        step, rt = self.active.steps[self.index], self.rt
        if step.kind == "cancel":
            await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
        elif step.kind == "notify":
            rt.clock.advance(NOTIFY_AT - rt.clock.now())
            await rt.tick()
        elif step.kind == "reply":
            await rt.handle_inbound(PID, step.text)
        else:
            rec = rt.journey_for_passenger(PID)
            assert rec is not None
            await (accept_offer if step.kind == "accept" else decline_offer)(rt, rec)
        if sales_tick:
            await self.tick_sales()
        self.index += 1
        return self.state()

    def advance_if_matches(self, op: str, passenger_id: str, value: str) -> bool:
        """Advance the step cursor when a manual turn matches the next scripted step (#322)."""
        if (
            self.active is not None
            and self.index < len(self.active.steps)
            and matches_step(self.active.steps[self.index], op, passenger_id, value)
        ):
            self.index += 1
            return True
        return False

    def state(self) -> ScenarioState:
        p, rt = self.active, self.rt
        assert p is not None
        rec: JourneyRecord | None = rt.journey_for_passenger(PID)
        events = rec.state.timeline if rec else []
        rebooked = [e for e in events if e.type == "rebooked"]
        seat_events = [e for e in events if e.type == "seat_held"]
        pnr = rt.world.pnrs[PNR]
        out = [m for m in rt.messages.get(PID, []) if m.direction == "outbound"]
        offers = rec.state.offers if rec else []
        return ScenarioState(
            scenario_id=p.id,
            step_index=self.index,
            total_steps=len(p.steps),
            done=self.index >= len(p.steps),
            last_step=p.steps[self.index - 1].label if self.index else None,
            next_step=p.steps[self.index].label if self.index < len(p.steps) else None,
            journey_id=rec.state.journey_id if rec else None,
            journey_status=str(rec.state.status) if rec else None,
            flight=rebooked[-1].payload["flight"] if rebooked else None,
            seat=pnr.seats[-1].seat if pnr.seats else None,
            seat_reason=seat_events[-1].payload.get("reason") if seat_events else None,
            offers_sent=sum(e.type == "offer_sent" for e in events),
            offer_status=passenger_offer_status(offers),
            charged=f"{rt.sim.partners.net_charged(PNR).quantize(Decimal('0.01'))}",
            needs_human=any(e.type == "needs_human" for e in events),
            last_message=out[-1].text if out else None,
        )
