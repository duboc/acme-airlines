"""Error responses never echo identifiers or raw exception text (#259)."""

import httpx
import pytest

from acme.api.app import create_app
from acme.engines.customer import EngineOperationError, build_customer_engine_app

PID = "P-0042-SECRET"
JID = "J-SECRET99"
PNR_LIKE = "ZX9Q4R"


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as c:
        yield c


def assert_clean(r, status, code):
    assert r.status_code == status
    body = r.text
    for secret in (PID, JID, PNR_LIKE, "SECRET"):
        assert secret not in body
    assert r.json()["code"] == code


async def test_unknown_passenger_messages_is_generic(client):
    assert_clean(await client.get(f"/whatsapp/{PID}/messages"), 404, "passenger_not_found")


async def test_unknown_passenger_reply_and_action_are_generic(client):
    assert_clean(
        await client.post(f"/whatsapp/{PID}/messages", json={"text": "hi"}),
        404,
        "passenger_not_found",
    )
    r = await client.post(f"/whatsapp/{PID}/actions", json={"action": "accept_offer"})
    assert_clean(r, 404, "passenger_not_found")


async def test_unknown_journey_is_generic(client):
    assert_clean(await client.get(f"/journeys/{JID}"), 404, "journey_not_found")
    assert_clean(await client.get(f"/journeys/{JID}/events"), 404, "journey_not_found")


async def test_unknown_flight_is_generic(client):
    r = await client.post(
        "/scenario/trigger-cancellation", json={"flight": "ZZ-SECRET", "reason": "x"}
    )
    assert_clean(r, 404, "flight_not_found")


async def test_engine_rpc_unknown_journey_is_generic():
    app = build_customer_engine_app()
    app.set_up()
    with pytest.raises(EngineOperationError) as e:
        await app.journey_events(JID)
    assert JID not in str(e.value)


async def test_engine_rpc_unhandled_exception_is_sanitized():
    app = build_customer_engine_app()
    app.set_up()

    async def boom(rt, *args):
        raise KeyError(f"passenger {PID} lookup failed")

    out = await app._api(boom, PID)
    assert out == {"error": {"status": 500, "detail": "internal error", "code": "internal_error"}}
    assert PID not in str(out)


async def test_engine_rpc_api_error_carries_code_not_id():
    app = build_customer_engine_app()
    app.set_up()
    out = await app.whatsapp_messages(PID)
    assert out["error"]["status"] == 404
    assert out["error"]["code"] == "passenger_not_found"
    assert PID not in str(out)
