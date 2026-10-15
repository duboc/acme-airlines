from fastapi.testclient import TestClient

from acme.api.app import create_local_app
from acme.domain.journey import JourneyState


def total(points, name, **match):
    return sum(v for a, v in points().get(name, []) if match.items() <= a.items())


def test_5xx_is_counted_and_4xx_is_not(metric_points):
    app = create_local_app()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("x")

    client = TestClient(app, raise_server_exceptions=False)
    before = total(metric_points, "acme.http.server.errors")
    assert client.get("/boom").status_code == 500
    assert client.get("/journeys/nope").status_code == 404
    assert total(metric_points, "acme.http.server.errors") - before == 1


def test_timeline_events_drive_offer_and_needs_human_counters(metric_points):
    state = JourneyState(journey_id="J1")
    b_h = total(metric_points, "acme.needs_human")
    b_o = total(metric_points, "acme.offers", status="declined")
    state.append_event("customer_agent", "needs_human", {"specialist": "closure"})
    state.append_event("passenger", "decline_offer", {})
    assert total(metric_points, "acme.needs_human") - b_h == 1
    assert total(metric_points, "acme.offers", status="declined") - b_o == 1


def test_an_accept_tap_alone_is_not_an_accepted_offer_only_a_confirmed_upgrade_is(metric_points):
    """#363: every tap counted, so refused and repeated taps made accepted + declined exceed the
    offers proposed."""
    state = JourneyState(journey_id="J2")
    before = total(metric_points, "acme.offers", status="accepted")
    state.append_event("passenger", "accept_offer", {})  # a tap, possibly refused
    state.append_event("passenger", "accept_offer", {})  # and a repeated one
    assert total(metric_points, "acme.offers", status="accepted") - before == 0
    state.append_event("customer_agent", "upgrade_confirmed", {"seat": "2A"})
    assert total(metric_points, "acme.offers", status="accepted") - before == 1


async def test_a_refused_stale_accept_in_upgrade_race_counts_no_accepted_offer(metric_points):
    import httpx

    from acme.api.app import create_app

    before = total(metric_points, "acme.offers", status="accepted")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as c:
        state = (await c.post("/scenarios/upgrade-race/start")).json()
        while not state["done"]:
            state = (await c.post("/scenarios/upgrade-race/step")).json()
        assert state["offer_status"] == "invalidated"
        await c.post("/whatsapp/P-MARIANA/actions", json={"action": "accept_offer"})  # again
    assert total(metric_points, "acme.offers", status="accepted") - before == 0


async def test_a_happy_path_conversion_counts_exactly_one_accepted_offer(metric_points):
    import httpx

    from acme.api.app import create_app

    before = total(metric_points, "acme.offers", status="accepted")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as c:
        state = (await c.post("/scenarios/happy-path/start")).json()
        while not state["done"]:
            state = (await c.post("/scenarios/happy-path/step")).json()
        assert state["offer_status"] == "accepted"
    assert total(metric_points, "acme.offers", status="accepted") - before == 1
