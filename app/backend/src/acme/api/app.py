"""FastAPI app factory. Contract: `app/backend/openapi.json` (see `acme.api.openapi`)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from acme.api.events import TimelineEvent
from acme.api.runtime import JourneyRecord, Runtime, WhatsAppMessage
from acme.customer.adk_agent import CARD_PATH, customer_agent_card
from acme.customer.conversion import NoSentOffer, accept_offer, decline_offer
from acme.customer.skills import CustomerSkills
from acme.domain.clock import Clock
from acme.domain.journey import OfferRecord
from acme.domain.models import PNR, Bag, HotelBooking, SeatAssignment, Segment, Voucher

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
SSE_POLL_SECONDS = 0.05


class TriggerCancellation(BaseModel):
    flight: str = Field(examples=["AC1234"])
    reason: str = Field(min_length=1, examples=["unscheduled maintenance"])


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


class JourneyDetail(JourneySummary):
    resolution_acknowledged: bool
    itinerary: list[Segment]
    seat: SeatAssignment | None
    bags: list[Bag]
    hotel: HotelBooking | None
    vouchers: list[Voucher]
    offer: OfferView | None
    verification: dict[str, bool] | None = None


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
    found = [e.payload["checks"] for e in rec.state.timeline if e.type == "verification_passed"]
    return found[-1] if found else None


def create_app(clock: Clock | None = None) -> FastAPI:
    app = FastAPI(title="ACME Airlines runtime API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=DEV_ORIGINS, allow_methods=["*"], allow_headers=["*"]
    )
    rt = Runtime(clock)
    app.state.runtime = rt

    def get_journey(journey_id: str) -> JourneyRecord:
        if journey_id not in rt.journeys:
            raise HTTPException(404, f"unknown journey {journey_id}")
        return rt.journeys[journey_id]

    def require_passenger(passenger_id: str) -> None:
        if passenger_id not in rt.world.passengers:
            raise HTTPException(404, f"unknown passenger {passenger_id}")

    @app.get(CARD_PATH, include_in_schema=False)
    async def agent_card(request: Request) -> dict:
        card = customer_agent_card(str(request.base_url))
        return card.model_dump(mode="json", by_alias=True, exclude_none=True)

    @app.post("/a2a", include_in_schema=False)
    async def a2a_endpoint(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        return await CustomerSkills(rt).handle_a2a(body if isinstance(body, dict) else {})

    @app.post("/scenario/reset", tags=["scenario"])
    async def reset() -> dict[str, str]:
        rt.reset()
        return {"status": "reset"}

    @app.post("/scenario/trigger-cancellation", tags=["scenario"])
    async def trigger_cancellation(body: TriggerCancellation) -> TriggerResult:
        if body.flight not in rt.world.flights:
            raise HTTPException(404, f"unknown flight {body.flight}")
        return TriggerResult(journey_ids=await rt.trigger_cancellation(body.flight, body.reason))

    @app.get("/journeys", tags=["journeys"])
    async def list_journeys() -> list[JourneySummary]:
        return [
            JourneySummary(journey_id=j, passenger_id=r.passenger_id, status=r.state.status)
            for j, r in rt.journeys.items()
        ]

    @app.get("/journeys/{journey_id}", tags=["journeys"])
    async def journey_detail(journey_id: str) -> JourneyDetail:
        await rt.tick()
        rec = get_journey(journey_id)
        pnr: PNR = rt.world.pnrs[rec.pnr_locator]
        offers = [o for o in rec.state.offers if o.status not in ("invalidated", "rejected")]
        return JourneyDetail(
            journey_id=journey_id,
            passenger_id=rec.passenger_id,
            status=rec.state.status,
            resolution_acknowledged=rec.state.resolution_acknowledged,
            itinerary=pnr.segments,
            seat=pnr.seats[0] if pnr.seats else None,
            bags=pnr.bags,
            hotel=rec.hotel,
            vouchers=rec.vouchers,
            offer=_offer_view(offers[-1] if offers else None),
            verification=_verification(rec),
        )

    @app.get(
        "/journeys/{journey_id}/events",
        tags=["journeys"],
        response_model=list[TimelineEvent],
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def journey_events(
        journey_id: str,
        limit: int | None = Query(None, ge=1, description="Close the stream after N events."),
    ) -> StreamingResponse:
        """SSE stream: one `data:` frame per timeline event (a `TimelineEvent`, by `type`)."""
        rec = get_journey(journey_id)

        async def stream() -> AsyncIterator[str]:
            sent = 0
            while limit is None or sent < limit:
                timeline = rec.state.timeline
                if sent < len(timeline):
                    event = timeline[sent]
                    sent += 1
                    yield f"data: {json.dumps(event.model_dump(mode='json'))}\n\n"
                else:
                    await asyncio.sleep(SSE_POLL_SECONDS)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/whatsapp/{passenger_id}/messages", tags=["whatsapp"])
    async def whatsapp_messages(passenger_id: str) -> list[WhatsAppMessage]:
        require_passenger(passenger_id)
        await rt.tick()
        return rt.messages.get(passenger_id, [])

    @app.post("/whatsapp/{passenger_id}/messages", status_code=201, tags=["whatsapp"])
    async def whatsapp_reply(passenger_id: str, body: PassengerReply) -> WhatsAppMessage:
        require_passenger(passenger_id)
        return await rt.handle_inbound(passenger_id, body.text)

    @app.post("/whatsapp/{passenger_id}/actions", tags=["whatsapp"])
    async def whatsapp_action(passenger_id: str, body: PassengerAction) -> ActionResult:
        require_passenger(passenger_id)
        rec = rt.journey_for_passenger(passenger_id)
        if rec is None:
            raise HTTPException(409, "no offer has been sent to this passenger")
        try:
            if body.action == "accept_offer":
                return ActionResult(offer_status=await accept_offer(rt, rec))
            return ActionResult(offer_status=await decline_offer(rt, rec))
        except NoSentOffer:
            raise HTTPException(409, "no offer has been sent to this passenger") from None

    return app
