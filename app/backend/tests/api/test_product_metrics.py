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
