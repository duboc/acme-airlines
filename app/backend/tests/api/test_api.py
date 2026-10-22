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


async def test_trigger_cancellation_reason_length_limits(client):
    """#574: reason is 1..1000 chars; the engine route shares this model, so it validates alike."""
    url = "/scenario/trigger-cancellation"
    ok = await client.post(url, json={"flight": "AC1234", "reason": "x" * 1000})
    assert ok.status_code == 200
    assert (
        await client.post(url, json={"flight": "AC1234", "reason": "x" * 1001})
    ).status_code == 422
    assert (await client.post(url, json={"flight": "AC1234", "reason": ""})).status_code == 422


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
    frames = [f for f in r.text.split("\n\n") if f and not f.startswith("retry:")]
    assert all(f.startswith("id: ") and "\ndata: " in f for f in frames)
    events = [json.loads(f.split("\ndata: ", 1)[1]) for f in frames]
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
    dup = await client.post(
        "/scenario/trigger-cancellation", json={"flight": "AC1234", "reason": "maintenance"}
    )
    assert dup.json() == {"journey_ids": []}
    empty = await client.post(
        "/scenario/trigger-cancellation", json={"flight": "AC1260", "reason": "crew"}
    )
    assert empty.json() == {"journey_ids": []}
    secondary = await client.post(
        "/scenario/trigger-cancellation", json={"flight": "AC1240", "reason": "crew"}
    )
    assert secondary.json() == {"journey_ids": [jid]}


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


async def test_action_accept_is_refused_by_the_guardrail_unless_resolved(client, app):
    from .helpers import put_sent_offer

    (jid,) = await trigger(client)
    put_sent_offer(app.state.runtime.journeys[jid].state)
    r = await client.post(f"/whatsapp/{PID}/actions", json={"action": "accept_offer"})
    # the journey is not RESOLVED: the accept-time re-check refuses, nothing is charged (#28);
    # the success path is covered in tests/customer/test_conversion.py
    assert r.status_code == 200 and r.json()["offer_status"] == "invalidated"
    assert app.state.runtime.sim.partners.charges == {}


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
    app = create_app(clock)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        (jid,) = await trigger(c)
        assert (await c.get(f"/whatsapp/{PID}/messages")).json() == []  # 22:03, quiet
        clock.advance(timedelta(minutes=6))  # 22:09
        assert (await c.get(f"/whatsapp/{PID}/messages")).json() == []  # GETs are read-only (#228)
        await app.state.loop.step()  # what the background loop does on its own
        msgs = (await c.get(f"/whatsapp/{PID}/messages")).json()
        assert len(msgs) == 1 and "AC1240" in msgs[0]["text"]
        assert "meal_voucher" in msgs[0]["attachments"]
        assert len((await c.get(f"/whatsapp/{PID}/messages")).json()) == 1  # exactly one
        r = await c.post(f"/whatsapp/{PID}/messages", json={"text": "Perfect, thanks!"})
        assert r.status_code == 201
        assert (await c.get(f"/journeys/{jid}")).json()["resolution_acknowledged"] is True


async def test_journey_detail_shows_the_checks_of_a_failed_verification(client, app):
    (jid,) = await trigger(client)
    rec = app.state.runtime.journeys[jid]
    rec.state.append_event(
        "customer_agent", "verification_failed",
        {"failed": ["hotel"], "details": {}, "checks": {"flight": True, "hotel": False}},
    )  # fmt: skip
    detail = (await client.get(f"/journeys/{jid}")).json()
    assert detail["verification"] == {"flight": True, "hotel": False}  # was null


# --- JourneyDetail: bag routing, active transfers, needs_human (#477) ---------------------------
async def detail(client, jid):
    r = await client.get(f"/journeys/{jid}")
    assert r.status_code == 200, r.text
    return r.json()


async def test_bag_routing_follows_worldtracer_through_the_replan(client, app):
    (jid,) = await trigger(client)
    assert (await detail(client, jid))["bags"] == [
        {"tag": "BAG0001", "checked": True, "routing": "AC1240"}
    ]
    rt = app.state.runtime
    await rt.handle_inbound(PID, "Can I leave after 10:00 AM instead?")
    assert (await detail(client, jid))["bags"][0]["routing"] == "AC1252"


async def test_transfers_list_only_the_active_pickups_after_a_replan(client, app):
    (jid,) = await trigger(client)
    before = (await detail(client, jid))["transfers"]
    assert [(t["leg"], t["origin"], t["destination"]) for t in before] == [
        ("airport_to_hotel", "GRU", "HT8842"),
        ("hotel_to_airport", "HT8842", "GRU"),
    ]
    assert all(
        set(t) == {"transfer_ref", "leg", "origin", "destination", "pickup_at"} for t in before
    )
    await app.state.runtime.handle_inbound(PID, "Can I leave after 10:00 AM instead?")
    after = (await detail(client, jid))["transfers"]
    assert [t["leg"] for t in after] == ["airport_to_hotel", "hotel_to_airport"]  # 2, not 3
    assert after[0]["transfer_ref"] == before[0]["transfer_ref"]
    assert after[1]["transfer_ref"] != before[1]["transfer_ref"]  # the superseded pickup is gone
    assert after[1]["pickup_at"] > before[1]["pickup_at"]  # rescheduled for the later flight


async def test_a_resident_gets_a_single_home_transfer_in_the_detail(app, client):
    p = app.state.runtime.world.passengers[PID]
    p.origin_resident = True  # DOC-06: a transfer home, no hotel
    (jid,) = await trigger(client)
    d = await detail(client, jid)
    assert [(t["leg"], t["origin"], t["destination"]) for t in d["transfers"]] == [
        ("airport_to_home", "GRU", "HOME")
    ]
    assert d["hotel"] is None


async def test_needs_human_is_false_when_all_is_well_and_true_after_an_escalation(client, app):
    (jid,) = await trigger(client)
    assert (await detail(client, jid))["needs_human"] is False
    await app.state.runtime.handle_inbound(PID, "I would like a refund")  # Other: a person
    assert (await detail(client, jid))["needs_human"] is True


async def test_needs_human_is_true_when_the_hotel_is_unavailable(client, app):
    app.state.runtime.world.hotels_with_rooms.clear()
    (jid,) = await trigger(client)
    d = await detail(client, jid)
    assert d["needs_human"] is True and d["status"] != "RESOLVED"


async def test_needs_human_is_scoped_to_the_current_disruption(client, app):
    from acme.domain.journey import DisruptionStatus as S

    rt = app.state.runtime
    (jid,) = await trigger(client)
    rec = rt.journeys[jid]
    rec.state.append_event("customer_agent", "needs_human", {"specialist": "closure"})
    assert (await detail(client, jid))["needs_human"] is True
    rec.state.append_event("occ", "cancellation_detected", {"flight": "AC1240", "reason": "x"})
    assert rec.state.status in (S.RESOLVED, S.RESOLVING)
    assert (await detail(client, jid))["needs_human"] is False  # an earlier disruption's flag


async def test_detail_lists_the_rescheduled_pickup_after_a_same_day_secondary(client):  # #490
    from datetime import timedelta

    from acme.domain.clock import FixedClock
    from acme.domain.fixtures import DISRUPTION_DETECTED_AT

    app = create_app(FixedClock(DISRUPTION_DETECTED_AT))
    rt = app.state.runtime
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        (jid,) = await trigger(c)
        rt.clock.advance(timedelta(hours=8, minutes=57))  # the morning of AC1240
        assert (
            await c.post(
                "/scenario/trigger-cancellation", json={"flight": "AC1240", "reason": "weather"}
            )
        ).json() == {"journey_ids": [jid]}
        d = await detail(c, jid)
    assert [(t["leg"], t["pickup_at"]) for t in d["transfers"]] == [
        ("airport_to_hotel", "2026-10-09T23:03:00-03:00"),
        ("hotel_to_airport", "2026-10-10T08:40:00-03:00"),
    ]


@pytest.mark.parametrize("reason", ["unscheduled maintenance", "severe weather"])
async def test_a_manual_primary_trigger_on_the_live_clock_keeps_the_seeded_duty_of_care(
    client, reason
):  # #494
    r = await client.post(
        "/scenario/trigger-cancellation", json={"flight": "AC1234", "reason": reason}
    )
    (jid,) = r.json()["journey_ids"]
    d = await detail(client, jid)
    assert sorted(v["amount"] for v in d["vouchers"]) == ["12.00", "25.00"]
    assert [(t["leg"], t["pickup_at"]) for t in d["transfers"]] == [
        ("airport_to_hotel", "2026-10-09T23:03:00-03:00"),
        ("hotel_to_airport", "2026-10-10T05:15:00-03:00"),
    ]
