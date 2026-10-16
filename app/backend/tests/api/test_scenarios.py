"""Demo scenario presets (#145): one end-to-end test per preset, stepped through the API."""

import httpx
import pytest

from acme.api.app import create_app
from acme.api.openapi import render_openapi

IDS = ["happy-path", "declines-upgrade", "unsatisfiable-request", "seat-taken", "hotel-unavailable",
       "upgrade-race"]  # fmt: skip


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as c:
        yield c


async def run_to_end(client, sid):
    r = await client.post(f"/scenarios/{sid}/start")
    assert r.status_code == 200, r.text
    assert r.json()["step_index"] == 0 and not r.json()["done"]
    state = r.json()
    n = 0
    while not state["done"]:
        r = await client.post(f"/scenarios/{sid}/step")
        assert r.status_code == 200, r.text
        state = r.json()
        n += 1
    assert n == state["total_steps"]
    return state


async def test_list_presets(client):
    r = await client.get("/scenarios")
    assert [p["id"] for p in r.json()] == IDS
    assert all(p["title"] and p["steps"] and p["expectation"] for p in r.json())


async def test_happy_path(client):
    s = await run_to_end(client, "happy-path")
    assert (s["journey_status"], s["flight"], s["seat"]) == ("CLOSED", "AC1252", "2A")
    assert s["offer_status"] == "accepted" and s["charged"] == "135.00"


async def test_declines_upgrade_gets_no_further_offer(client):
    s = await run_to_end(client, "declines-upgrade")
    assert (s["flight"], s["seat"], s["charged"]) == ("AC1252", "14A", "0.00")
    assert s["offer_status"] == "declined" and s["offers_sent"] == 1


async def test_unsatisfiable_request_is_honest_and_keeps_ac1240(client):
    s = await run_to_end(client, "unsatisfiable-request")
    assert s["flight"] == "AC1240" and s["journey_status"] == "RESOLVED"
    assert "after 13:00" in s["last_message"] and "stays on AC1240" in s["last_message"]
    assert s["offers_sent"] == 0


async def test_seat_taken_gets_nearest_window_seat_with_explanation(client):
    s = await run_to_end(client, "seat-taken")
    assert s["flight"] == "AC1240" and s["seat"] == "15A"
    assert "14A unavailable on AC1240" in s["seat_reason"]


async def test_hotel_unavailable_needs_human_never_resolved_never_offered(client):
    s = await run_to_end(client, "hotel-unavailable")
    assert s["needs_human"] and s["journey_status"] != "RESOLVED"
    assert s["offers_sent"] == 0 and s["charged"] == "0.00"


async def test_upgrade_race_moves_no_money(client):
    s = await run_to_end(client, "upgrade-race")
    assert s["offer_status"] == "invalidated" and s["charged"] == "0.00" and s["seat"] == "14A"
    assert "not been charged" in s["last_message"]


async def test_start_resets_the_world(client):
    await run_to_end(client, "happy-path")
    s = (await client.post("/scenarios/seat-taken/start")).json()
    assert s["journey_id"] is None and s["charged"] == "0.00"
    assert (await client.get("/journeys")).json() == []


async def test_step_errors(client):
    assert (await client.post("/scenarios/happy-path/step")).status_code == 409  # not started
    assert (await client.post("/scenarios/nope/start")).status_code == 404
    await run_to_end(client, "happy-path")
    assert (await client.post("/scenarios/happy-path/step")).status_code == 409  # finished
    await client.post("/scenarios/seat-taken/start")
    assert (await client.post("/scenarios/happy-path/step")).status_code == 409  # other active


async def test_disabled_outside_fake_mode(client, monkeypatch):
    monkeypatch.setenv("LLM_MODE", "vertex")
    assert (await client.get("/scenarios")).status_code == 404
    assert (await client.post("/scenarios/happy-path/start")).status_code == 404
    assert (await client.post("/scenarios/happy-path/step")).status_code == 404


async def test_openapi_documents_scenarios(client):
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert {"/scenarios", "/scenarios/{scenario_id}/start", "/scenarios/{scenario_id}/step"} <= set(
        paths
    )
    assert render_openapi(create_app()).count("/scenarios") >= 3


async def test_journey_detail_keeps_an_offer_that_was_sent_and_then_invalidated(client):
    """#339: upgrade-race steps 5-6 invalidate the sent offer; the scenario state, the timeline and
    the chat say so, and `GET /journeys/{id}` returned `offer: null` (the console: "None")."""
    s = await run_to_end(client, "upgrade-race")
    assert s["offer_status"] == "invalidated"
    (journey,) = (await client.get("/journeys")).json()
    detail = (await client.get(f"/journeys/{journey['journey_id']}")).json()
    assert detail["offer"]["status"] == "invalidated"
    assert detail["offer"]["price"] == "135.00" and detail["offer"]["flight"] == "AC1252"


async def test_journey_detail_hides_an_offer_withdrawn_before_it_was_sent(client):
    await run_to_end(client, "upgrade-race")
    (journey,) = (await client.get("/journeys")).json()
    rec = client._transport.app.state.runtime.journeys[journey["journey_id"]]  # noqa: SLF001
    rec.state._timeline[:] = [e for e in rec.state.timeline if e.type != "offer_sent"]  # noqa: SLF001
    detail = (await client.get(f"/journeys/{journey['journey_id']}")).json()
    assert detail["offer"] is None  # it never reached the passenger


@pytest.mark.parametrize("sid", ["happy-path", "declines-upgrade"])
async def test_scripted_accept_or_decline_with_no_sent_offer_returns_409(
    client, metric_points, sid
):
    """#455 AC1: when a manual unhandled WhatsApp question opens needs_human and suppresses the
    upgrade offer under Rule 2, the scripted accept/decline step returns 409 no_offer_sent without
    advancing step_index or incrementing acme.http.server.errors."""
    before = list(metric_points().get("acme.http.server.errors", []))
    await client.post(f"/scenarios/{sid}/start")
    for _ in range(2):  # cancel, notify
        assert (await client.post(f"/scenarios/{sid}/step")).status_code == 200
    r_msg = await client.post(
        "/whatsapp/P-MARIANA/messages", json={"text": "Where do I claim my pet crate?"}
    )
    assert r_msg.status_code == 201
    for _ in range(2):  # after-10am, ack (offer suppressed by open needs_human request)
        s = (await client.post(f"/scenarios/{sid}/step")).json()
    assert s["step_index"] == 4 and s["offers_sent"] == 0 and s["needs_human"] is True

    r = await client.post(f"/scenarios/{sid}/step")
    assert r.status_code == 409
    assert r.json() == {
        "code": "no_offer_sent",
        "detail": "no offer has been sent to this passenger",
    }
    after = list(metric_points().get("acme.http.server.errors", []))
    assert after == before
    # A second attempt still sees step_index == 4 (not scenario_finished).
    r2 = await client.post(f"/scenarios/{sid}/step")
    assert r2.status_code == 409 and r2.json()["code"] == "no_offer_sent"
