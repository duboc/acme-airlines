"""FastAPI app factory. Contract: `app/backend/openapi.json` (see `acme.api.openapi`)."""

from __future__ import annotations

import contextlib
import os
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from acme import metrics
from acme.api.a2a_guard import SalesRouteGuard, sales_token_from_env
from acme.api.errors import ApiError, add_error_handlers, not_found
from acme.api.events import TimelineEvent
from acme.api.runtime import JourneyRecord, Runtime, WhatsAppMessage
from acme.api.scenarios import (
    BY_ID,
    PRESETS,
    PresetView,
    ScenarioRunner,
    ScenarioState,
    preset_view,
)
from acme.api.sse import event_stream, resume_from
from acme.customer.adk_agent import CARD_PATH, customer_agent_card
from acme.customer.conversion import NoSentOffer, accept_offer, decline_offer
from acme.customer.loop import CustomerLoop
from acme.customer.outreach import _current_disruption_events
from acme.customer.skills import CustomerSkills
from acme.domain.clock import Clock
from acme.domain.journey import OfferRecord
from acme.domain.models import PNR, Bag, HotelBooking, SeatAssignment, Segment, Voucher
from acme.llm.config import load_llm_mode
from acme.sim.dcs import dcs_state
from acme.sim.world import SimError

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
SSE_POLL_SECONDS = 0.05


class TriggerCancellation(BaseModel):
    flight: str = Field(examples=["AC1234"])
    reason: str = Field(min_length=1, max_length=1000, examples=["unscheduled maintenance"])


class TriggerResult(BaseModel):
    journey_ids: list[str]


class JourneySummary(BaseModel):
    journey_id: str
    passenger_id: str
    status: str


class OfferView(BaseModel):
    offer_id: str
    flight: str
    price: str
    standard_price: str
    status: str


class TransferView(BaseModel):
    """An active ground transfer (a superseded pickup is not listed). No passenger data."""

    transfer_ref: str
    leg: str
    origin: str
    destination: str
    pickup_at: str


class JourneyDetail(JourneySummary):
    resolution_acknowledged: bool
    itinerary: list[Segment]
    seat: SeatAssignment | None
    bags: list[Bag]
    hotel: HotelBooking | None
    vouchers: list[Voucher]
    offer: OfferView | None
    verification: dict[str, bool] | None = None
    transfers: list[TransferView] = Field(default_factory=list)
    needs_human: bool = False  # an open request or a needs_human entry in the CURRENT disruption


class PassengerReply(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class PassengerAction(BaseModel):
    action: Literal["accept_offer", "decline_offer"]


class ActionResult(BaseModel):
    offer_status: str


def _offer_view(rec: OfferRecord | None) -> OfferView | None:
    if rec is None:
        return None
    o = rec.offer
    return OfferView(
        offer_id=str(o.offer_id),
        flight=o.flight,
        price=str(o.price),
        standard_price=str(o.standard_price),
        status=rec.status,
    )


def _verification(rec: JourneyRecord) -> dict[str, bool] | None:
    # The latest verification, passed or failed: a failed one carries the check map too (#345).
    found = [
        e.payload["checks"]
        for e in rec.state.timeline
        if e.type in ("verification_passed", "verification_failed") and "checks" in e.payload
    ]
    return found[-1] if found else None


def journey_summary(rec: JourneyRecord) -> JourneySummary:
    return JourneySummary(
        journey_id=rec.state.journey_id, passenger_id=rec.passenger_id, status=rec.state.status
    )


def _bag_routing(rt: Runtime, tag: str) -> str | None:
    try:
        return dcs_state(rt.sim).bag(tag)["routing"]
    except SimError:  # a tag WorldTracer does not know
        return None


def _active_transfers(rt: Runtime, rec: JourneyRecord) -> list[TransferView]:
    """The journey's `transfer_arranged` entries that the concierge still holds, in order: a
    pickup cancelled when the passenger was replanned (#398) is gone from `partners.transfers`."""
    held = rt.sim.partners.transfers
    return [
        TransferView(
            transfer_ref=ref,
            leg=e.payload["leg"],
            origin=held[ref]["origin"],
            destination=held[ref]["destination"],
            pickup_at=held[ref]["pickup_at"],
        )
        for e in rec.state.timeline
        if e.type == "transfer_arranged" and (ref := e.payload["transfer_ref"]) in held
    ]


def journey_detail_view(rt: Runtime, rec: JourneyRecord) -> JourneyDetail:
    """The `GET /journeys/{id}` body; shared with the Customer Agent Engine operations (#162)."""
    pnr: PNR = rt.world.pnrs[rec.pnr_locator]
    # An invalidated offer that WAS sent stays visible as such (upgrade-race steps 5 and 6: the
    # scenario state, the timeline and the chat all say so, #339); one withdrawn before it was ever
    # sent, and a rejected one, never reached the passenger.
    sent_ids = {e.payload.get("offer_id") for e in rec.state.timeline if e.type == "offer_sent"}
    offers = [
        o
        for o in rec.state.offers
        if o.status != "rejected"
        and (o.status != "invalidated" or str(o.offer.offer_id) in sent_ids)
    ]
    return JourneyDetail(
        journey_id=rec.state.journey_id,
        passenger_id=rec.passenger_id,
        status=rec.state.status,
        resolution_acknowledged=rec.state.resolution_acknowledged,
        itinerary=pnr.segments,
        seat=pnr.seats[0] if pnr.seats else None,
        bags=[b.model_copy(update={"routing": _bag_routing(rt, b.tag)}) for b in pnr.bags],
        hotel=rec.hotel,
        vouchers=rec.vouchers,
        offer=_offer_view(offers[-1] if offers else None),
        verification=_verification(rec),
        transfers=_active_transfers(rt, rec),
        needs_human=bool(rec.state.open_requests)
        or any(e.type == "needs_human" for e in _current_disruption_events(rec)),
    )


def require_passenger(rt: Runtime, passenger_id: str) -> None:
    if passenger_id not in rt.world.passengers:
        raise not_found("passenger", passenger_id)


async def do_trigger(rt: Runtime, flight: str, reason: str) -> TriggerResult:
    if flight not in rt.world.flights:
        raise not_found("flight", flight)
    return TriggerResult(journey_ids=await rt.trigger_cancellation(flight, reason))


async def do_messages(rt: Runtime, passenger_id: str) -> list[WhatsAppMessage]:
    require_passenger(rt, passenger_id)
    return rt.messages.get(passenger_id, [])


async def do_reply(rt: Runtime, passenger_id: str, text: str) -> WhatsAppMessage:
    require_passenger(rt, passenger_id)
    return await rt.handle_inbound(passenger_id, text)


async def do_action(rt: Runtime, passenger_id: str, action: str) -> ActionResult:
    """Accept/decline go through `acme.customer.conversion` (guardrail re-check included)."""
    require_passenger(rt, passenger_id)
    rec = rt.journey_for_passenger(passenger_id)
    if rec is None:
        raise ApiError(409, "no_offer_sent", "no offer has been sent to this passenger")
    try:
        if action == "accept_offer":
            return ActionResult(offer_status=await accept_offer(rt, rec))
        return ActionResult(offer_status=await decline_offer(rt, rec))
    except NoSentOffer:
        raise ApiError(409, "no_offer_sent", "no offer has been sent to this passenger") from None


def create_app(clock: Clock | None = None) -> FastAPI:
    if (os.environ.get("ENGINE_MODE") or "local") == "agent-engine":
        from acme.api.engine_backend import create_engine_app  # noqa: PLC0415

        return create_engine_app()  # fails fast without AGENT_ENGINE_* (#164)
    return create_local_app(clock)


def create_a2a_app(rt: Runtime, sales_token: str | None = None) -> FastAPI:
    """The A2A listener: agent card and `/a2a` only, no passenger route exists on it."""
    app = FastAPI(title="ACME Customer A2A", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = rt
    app.add_middleware(SalesRouteGuard, sales_token=sales_token or sales_token_from_env(os.environ))

    @app.get(CARD_PATH, include_in_schema=False)
    async def agent_card(request: Request) -> dict:
        card = customer_agent_card(str(request.base_url))
        return card.model_dump(mode="json", by_alias=True, exclude_none=True)

    @app.post("/a2a", include_in_schema=False)
    async def a2a_endpoint(request: Request) -> dict:
        return await _handle_a2a(rt, request)

    return app


async def _handle_a2a(rt: Runtime, request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    return await CustomerSkills(rt).handle_a2a(body if isinstance(body, dict) else {})


def create_local_app(clock: Clock | None = None, sales_token: str | None = None) -> FastAPI:
    rt = Runtime(clock)
    loop = CustomerLoop(rt)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        # The runtime acts on its own (OCC subscription, scheduled outreach, no-offer closure);
        # GET endpoints are read-only (#228). ACME_CUSTOMER_LOOP=0 turns it off.
        if os.environ.get("ACME_CUSTOMER_LOOP", "1") != "0":
            loop.start()
        try:
            yield
        finally:
            await loop.stop()

    app = FastAPI(title="ACME Airlines runtime API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(SalesRouteGuard, sales_token=sales_token or sales_token_from_env(os.environ))
    app.add_middleware(
        CORSMiddleware, allow_origins=DEV_ORIGINS, allow_methods=["*"], allow_headers=["*"]
    )
    metrics.add_http_metrics(app)
    app.state.runtime = rt
    app.state.loop = loop
    add_error_handlers(app)

    def get_journey(journey_id: str) -> JourneyRecord:
        if journey_id not in rt.journeys:
            raise not_found("journey", journey_id)
        return rt.journeys[journey_id]

    @app.get(CARD_PATH, include_in_schema=False)
    async def agent_card(request: Request) -> dict:
        card = customer_agent_card(str(request.base_url))
        return card.model_dump(mode="json", by_alias=True, exclude_none=True)

    @app.post("/a2a", include_in_schema=False)
    async def a2a_endpoint(request: Request) -> dict:
        return await _handle_a2a(rt, request)

    @app.post("/scenario/reset", tags=["scenario"])
    async def reset() -> dict[str, str]:
        runner.reset()
        return {"status": "reset"}

    @app.post("/scenario/trigger-cancellation", tags=["scenario"])
    async def trigger_cancellation(body: TriggerCancellation) -> TriggerResult:
        runner.end_preset(body.flight)  # primary: full reset (#321); secondary: keep the journey
        return await do_trigger(rt, body.flight, body.reason)

    runner = ScenarioRunner(rt)

    def require_demo_mode() -> None:
        if load_llm_mode(os.environ) != "fake":  # presets are a demo affordance (fake mode only)
            raise HTTPException(404, "scenario presets are disabled outside LLM_MODE=fake")

    @app.get("/scenarios", tags=["scenarios"])
    async def list_scenarios() -> list[PresetView]:
        require_demo_mode()
        return [preset_view(p) for p in PRESETS]

    @app.post("/scenarios/{scenario_id}/start", tags=["scenarios"])
    async def start_scenario(scenario_id: str) -> ScenarioState:
        require_demo_mode()
        if scenario_id not in BY_ID:
            raise not_found("scenario", scenario_id)
        return runner.start(BY_ID[scenario_id])

    @app.post("/scenarios/{scenario_id}/step", tags=["scenarios"])
    async def step_scenario(scenario_id: str) -> ScenarioState:
        """Advance the next scripted passenger action; the response is the expected next state."""
        require_demo_mode()
        if scenario_id not in BY_ID:
            raise not_found("scenario", scenario_id)
        if runner.active is None or runner.active.id != scenario_id:
            raise ApiError(409, "scenario_not_started", "scenario is not started")
        if runner.index >= len(runner.active.steps):
            raise ApiError(409, "scenario_finished", "scenario is finished; start it again")
        try:
            return await runner.step()
        except NoSentOffer:
            raise ApiError(
                409, "no_offer_sent", "no offer has been sent to this passenger"
            ) from None

    @app.get("/journeys", tags=["journeys"])
    async def list_journeys() -> list[JourneySummary]:
        return [journey_summary(r) for r in rt.journeys.values()]

    @app.get("/journeys/{journey_id}", tags=["journeys"])
    async def journey_detail(journey_id: str) -> JourneyDetail:
        return journey_detail_view(rt, get_journey(journey_id))

    @app.get(
        "/journeys/{journey_id}/events",
        tags=["journeys"],
        response_model=list[TimelineEvent],
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def journey_events(
        request: Request,
        journey_id: str,
        limit: int | None = Query(None, ge=1, description="Close the stream after N events."),
    ) -> StreamingResponse:
        """SSE stream: one `data:` frame per timeline event (a `TimelineEvent`, by `type`).

        It ends when the client goes away, when a new scenario replaced this journey, and after a
        bounded time; idle, it sends `: keep-alive` comments (#305)."""
        rec = get_journey(journey_id)

        async def events(sent: int) -> list[dict]:
            return [e.model_dump(mode="json") for e in rec.state.timeline[sent:]]

        start_after = resume_from(request.headers.get("last-event-id"))
        stream = event_stream(
            request,
            events,
            limit=limit,
            poll_seconds=SSE_POLL_SECONDS,
            still_open=lambda: rt.journeys.get(journey_id) is rec,
            start_after=start_after,
            first_batch=await events(start_after),  # read before the 200: a failure is a status
        )
        return StreamingResponse(stream, media_type="text/event-stream")

    @app.get("/whatsapp/{passenger_id}/messages", tags=["whatsapp"])
    async def whatsapp_messages(passenger_id: str) -> list[WhatsAppMessage]:
        return await do_messages(rt, passenger_id)

    @app.post("/whatsapp/{passenger_id}/messages", status_code=201, tags=["whatsapp"])
    async def whatsapp_reply(passenger_id: str, body: PassengerReply) -> WhatsAppMessage:
        msg = await do_reply(rt, passenger_id, body.text)
        if runner.active is not None:
            runner.advance_if_matches("whatsapp_reply", passenger_id, body.text)
        await runner.tick_sales()  # a manual reply is acknowledged with or without a preset (#485)
        return msg

    @app.post("/whatsapp/{passenger_id}/actions", tags=["whatsapp"])
    async def whatsapp_action(passenger_id: str, body: PassengerAction) -> ActionResult:
        res = await do_action(rt, passenger_id, body.action)
        if runner.active is not None:
            runner.advance_if_matches("whatsapp_action", passenger_id, body.action)
        return res

    return app
