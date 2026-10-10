import asyncio
import json
from pathlib import Path

import httpx
import pytest

from acme.api.app import create_app
from acme.api.openapi import render_openapi

CONTRACT = Path(__file__).resolve().parents[2] / "openapi.json"
PID = "P-MARIANA"


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def trigger(client, flight="AC1234"):
    r = await client.post(
        "/scenario/trigger-cancellation", json={"flight": flight, "reason": "maintenance"}
    )
    assert r.status_code == 200, r.text
    return r.json()["journey_ids"]


async def test_openapi_matches_committed_contract(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    committed = json.loads(CONTRACT.read_text())
    assert r.json() == committed, "run `uv run python -m acme.api.openapi` to regenerate"
    assert CONTRACT.read_text() == render_openapi(create_app())


async def test_trigger_creates_journey_for_affected_passenger(client):
    ids = await trigger(client)
    assert len(ids) == 1
    r = await client.get("/journeys")
    assert [j["journey_id"] for j in r.json()] == ids


async def test_trigger_unknown_flight_404_and_bad_body_422(client):
    r = await client.post("/scenario/trigger-cancellation", json={"flight": "ZZ9", "reason": "x"})
    assert r.status_code == 404
    r = await client.post("/scenario/trigger-cancellation", json={"flight": "AC1234"})
    assert r.status_code == 422


async def test_journey_detail(client):
    (jid,) = await trigger(client)
    r = await client.get(f"/journeys/{jid}")
    assert r.status_code == 200
    d = r.json()
    assert d["journey_id"] == jid
    assert d["passenger_id"] == PID
    assert d["status"] == "RESOLVED"
    assert [s["flight_number"] for s in d["itinerary"]] == ["AC1240", "AC0800"]
    assert d["seat"]["seat"] == "14A"
    assert d["bags"][0]["tag"] == "BAG0001"
    assert d["hotel"]["hotel_id"] == "HT8842" and d["vouchers"] and d["offer"] is None


async def test_journey_404(client):
    assert (await client.get("/journeys/J-00000000")).status_code == 404
    assert (await client.get("/journeys/J-00000000/events")).status_code == 404


async def test_reset_returns_world_to_fixture(client):
    await trigger(client)
    await client.post(f"/whatsapp/{PID}/messages", json={"text": "hi"})
    r = await client.post("/scenario/reset")
    assert r.status_code == 200
    assert (await client.get("/journeys")).json() == []
    assert (await client.get(f"/whatsapp/{PID}/messages")).json() == []


async def test_sse_streams_existing_and_new_events(client, app):
    (jid,) = await trigger(client)

    async def append_later():
        await asyncio.sleep(0.1)
        app.state.runtime.journeys[jid].state.append_event("test", "ping", {"n": 1})

    n = len(app.state.runtime.journeys[jid].state.timeline)
    task = asyncio.create_task(append_later())
    r = await asyncio.wait_for(client.get(f"/journeys/{jid}/events?limit={n + 1}"), timeout=5)
    await task
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = [f for f in r.text.split("\n\n") if f]
    assert all(f.startswith("data: ") for f in frames)
    events = [json.loads(f[len("data: ") :]) for f in frames]
    types = [e["type"] for e in events]
    assert types[:3] == ["cancellation_detected", "status_changed", "profile_loaded"]
    assert "rebooked" in types and types[-1] == "ping"
    assert events[2]["payload"]["tier"] == "diamond"
    assert events[-1]["payload"] == {"n": 1}


async def test_whatsapp_thread_is_silent_after_trigger(client):
    await trigger(client)  # quiet hours: nothing is sent until the journey is RESOLVED
    assert (await client.get(f"/whatsapp/{PID}/messages")).json() == []


async def test_trigger_reflects_real_agent_state(client):
    (jid,) = await trigger(client)
    d = (await client.get(f"/journeys/{jid}")).json()
    assert d["status"] == "RESOLVED"
    r = await client.post(
        "/scenario/trigger-cancellation", json={"flight": "AC1240", "reason": "crew"}
    )
    assert r.json() == {"journey_ids": []}


async def test_whatsapp_unknown_passenger_404(client):
    assert (await client.get("/whatsapp/P-NOPE/messages")).status_code == 404
    r = await client.post("/whatsapp/P-NOPE/messages", json={"text": "x"})
    assert r.status_code == 404
    r = await client.post("/whatsapp/P-NOPE/actions", json={"action": "accept_offer"})
    assert r.status_code == 404


async def test_whatsapp_passenger_reply_recorded_and_journey_event(client, app):
    (jid,) = await trigger(client)
    r = await client.post(f"/whatsapp/{PID}/messages", json={"text": "Can I leave after 10am?"})
    assert r.status_code == 201
    assert r.json()["direction"] == "inbound"
    msgs = (await client.get(f"/whatsapp/{PID}/messages")).json()
    assert [m["direction"] for m in msgs][-2:] == ["inbound", "outbound"]
    types = [e.type for e in app.state.runtime.journeys[jid].state.timeline]
    assert "passenger_message" in types


async def test_whatsapp_empty_text_422(client):
    r = await client.post(f"/whatsapp/{PID}/messages", json={"text": ""})
    assert r.status_code == 422


async def test_action_without_sent_offer_is_409(client):
    await trigger(client)
    r = await client.post(f"/whatsapp/{PID}/actions", json={"action": "accept_offer"})
    assert r.status_code == 409


async def test_action_accept_sent_offer(client, app):
    from .helpers import put_sent_offer

    (jid,) = await trigger(client)
    put_sent_offer(app.state.runtime.journeys[jid].state)
    r = await client.post(f"/whatsapp/{PID}/actions", json={"action": "accept_offer"})
    assert r.status_code == 200 and r.json()["offer_status"] == "accepted"
    d = (await client.get(f"/journeys/{jid}")).json()
    assert d["offer"]["status"] == "accepted"


async def test_action_bad_value_422(client):
    r = await client.post(f"/whatsapp/{PID}/actions", json={"action": "buy_everything"})
    assert r.status_code == 422


async def test_cors_for_vite_dev_server(client):
    r = await client.options(
        "/journeys",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_journey_detail_reflects_rebuilt_flight_seat_and_bag(client):
    (jid,) = await trigger(client)
    d = (await client.get(f"/journeys/{jid}")).json()
    assert [s["flight_number"] for s in d["itinerary"]] == ["AC1240", "AC0800"]
    assert (d["seat"]["flight_number"], d["seat"]["seat"]) == ("AC1240", "14A")
    assert d["status"] == "RESOLVED"


async def test_notification_and_acknowledgement_through_the_api():
    from datetime import timedelta

    from acme.domain.clock import FixedClock
    from acme.domain.fixtures import DISRUPTION_DETECTED_AT

    clock = FixedClock(DISRUPTION_DETECTED_AT)
    transport = httpx.ASGITransport(app=create_app(clock))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        (jid,) = await trigger(c)
        assert (await c.get(f"/whatsapp/{PID}/messages")).json() == []  # 22:03, quiet
        clock.advance(timedelta(minutes=6))  # 22:09
        msgs = (await c.get(f"/whatsapp/{PID}/messages")).json()
        assert len(msgs) == 1 and "AC1240" in msgs[0]["text"]
        assert "meal_voucher" in msgs[0]["attachments"]
        assert len((await c.get(f"/whatsapp/{PID}/messages")).json()) == 1  # exactly one
        r = await c.post(f"/whatsapp/{PID}/messages", json={"text": "Perfect, thanks!"})
        assert r.status_code == 201
        assert (await c.get(f"/journeys/{jid}")).json()["resolution_acknowledged"] is True
