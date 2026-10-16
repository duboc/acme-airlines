"""Customer engine demo operations (#162): parity with the API, vertex-mode presets, guardrails."""

import socket
from types import SimpleNamespace as NS

import httpx
import pytest

from acme.api.app import create_app
from acme.engines.customer import EngineOperationError, build_customer_engine_app
from acme.llm import FakeLlm

IDS = ["happy-path", "declines-upgrade", "unsatisfiable-request", "seat-taken", "hotel-unavailable",
       "upgrade-race"]  # fmt: skip
MARKER = "last_message"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(self, address, *a, **k):
        raise AssertionError(f"network call blocked: {address}")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.delenv("LLM_MODE", raising=False)


def engine(llm=None, monkeypatch=None):
    if llm is not None:
        monkeypatch.setattr("acme.customer.runtime.build_llm", lambda *a, **k: llm)
    app = build_customer_engine_app()
    app.set_up()
    return app


async def drive(app):
    """Step a started scenario to the end, doing the Sales tick the demo would drive."""
    state = app.scenario_state()
    while not state["done"]:
        state = await app.scenario_step()
        await app.scenarios.radar.tick()
    return app.scenario_state()


async def api_run(sid):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as c:
        state = (await c.post(f"/scenarios/{sid}/start")).json()
        while not state["done"]:
            state = (await c.post(f"/scenarios/{sid}/step")).json()
        journeys = (await c.get("/journeys")).json()
        detail = (
            (await c.get(f"/journeys/{journeys[0]['journey_id']}")).json() if journeys else None
        )
        return state, journeys, detail


def stable(detail):
    """Booking, voucher and transfer ids are random per run; everything else must match."""
    hotel = detail["hotel"] and {k: v for k, v in detail["hotel"].items() if k != "booking_id"}
    vouchers = [{k: v for k, v in x.items() if k != "voucher_id"} for x in detail["vouchers"]]
    offer = detail["offer"] and {k: v for k, v in detail["offer"].items() if k != "offer_id"}
    transfers = [{k: v for k, v in x.items() if k != "transfer_ref"} for x in detail["transfers"]]
    return {**detail, "hotel": hotel, "vouchers": vouchers, "offer": offer, "transfers": transfers}


def assert_guardrails(app):
    """Same invariants as tests/governance/test_outcome.py, on the engine's runtime."""
    rt = app.runtime
    for rec in rt.journeys.values():
        tl = rec.state.timeline
        sent = [i for i, e in enumerate(tl) if e.type == "offer_sent"]
        assert len(sent) <= 1  # zero pressure: one offer at most
        acks = [i for i, e in enumerate(tl) if e.type == "resolution_acknowledged"]
        if sent:
            assert acks and acks[-1] < sent[0]  # offer only after the last resolution is acked
            resolved = [
                i for i, e in enumerate(tl)
                if e.type == "status_changed" and e.payload["to"] in ("RESOLVED", "CLOSED")
            ]  # fmt: skip
            assert resolved and resolved[-1] > acks[-1] or resolved[-1] < sent[0]
        assert all(o.offer.origin == "sales" and o.offer.optional for o in rec.state.offers)
    buttons = [
        m for m in rt.messages.get("P-MARIANA", []) if m.direction == "outbound" and m.buttons
    ]
    offers = sum(len(r.state.offers) for r in rt.journeys.values())
    assert len(buttons) == offers  # every button message is a structured offer


@pytest.mark.parametrize("sid", IDS)
async def test_engine_state_and_journey_equal_the_api(sid):
    app = engine()
    app.scenario_start(sid)
    state = await drive(app)
    api_state, api_journeys, api_detail = await api_run(sid)
    assert {k: v for k, v in state.items() if k != "tape"} == api_state  # tape: caller only
    assert app.journeys_list() == api_journeys
    if api_detail:
        assert stable(await app.journey_detail(api_journeys[0]["journey_id"])) == stable(api_detail)
    assert_guardrails(app)


def test_list_and_events_operations():
    app = engine()
    assert [p["id"] for p in app.scenarios_list()] == IDS
    with pytest.raises(EngineOperationError):
        app.scenario_start("nope")
    with pytest.raises(EngineOperationError):
        app.scenario_state()


async def test_events_since_and_unknown_journey():
    app = engine()
    app.scenario_start("happy-path")
    await app.scenario_step()
    (jid,) = [j["journey_id"] for j in app.journeys_list()]
    everything = await app.journey_events(jid)
    assert everything and await app.journey_events(jid, since=len(everything)) == []
    assert await app.journey_events(jid, since=1) == everything[1:]
    with pytest.raises(EngineOperationError):
        await app.journey_events("J-nope")


async def test_step_past_the_end_is_rejected():
    app = engine()
    app.scenario_start("seat-taken")
    await drive(app)
    with pytest.raises(EngineOperationError, match="finished"):
        await app.scenario_step()


async def test_step_runs_as_an_adk_turn_with_scenario_span(monkeypatch):
    seen = {}

    class Span:
        def set_attribute(self, k, v):
            seen[k] = v

    monkeypatch.setattr("acme.engines.customer.trace", NS(get_current_span=lambda: Span()))
    app = engine()
    app.scenario_start("seat-taken")
    await app.scenario_step()
    assert seen == {"acme.scenario": "seat-taken", "acme.step": 0}


@pytest.fixture
def vertex(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "vertex")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "acme-airlines-factory")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")


STRUCTURAL = ("scenario_id", "step_index", "done", "journey_status", "flight", "seat",
              "offers_sent", "offer_status", "charged", "needs_human")  # fmt: skip


@pytest.mark.parametrize("sid", IDS)
async def test_presets_work_on_the_engine_with_vertex_mode_and_match_structurally(
    sid, vertex, monkeypatch
):
    app = engine(FakeLlm(), monkeypatch)  # LLM port faked: no network
    assert app.llm_mode == "vertex"
    app.scenario_start(sid)
    state = await drive(app)
    api_state, *_ = await api_run_fake(sid, monkeypatch)
    assert {k: state[k] for k in STRUCTURAL} == {k: api_state[k] for k in STRUCTURAL}
    assert_guardrails(app)


async def api_run_fake(sid, monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fake")
    return await api_run(sid)


class OmitsFacts(FakeLlm):
    async def compose_passenger_message(self, context):
        return "Good news, everything is sorted out."  # none of the resolution facts


async def test_llm_message_missing_a_fact_falls_back_to_the_template_on_the_engine(
    vertex, monkeypatch
):
    app = engine(OmitsFacts(), monkeypatch)
    app.scenario_start("seat-taken")
    state = await drive(app)
    (jid,) = [j["journey_id"] for j in app.journeys_list()]
    notices = [e for e in await app.journey_events(jid) if e["type"] == "notification_sent"]
    assert [n["payload"]["source"] for n in notices] == ["template"]
    assert "sorted out" not in state["last_message"] and "AC1240" in state["last_message"]


async def test_api_app_keeps_the_404_rule_outside_fake_mode(vertex, monkeypatch):
    monkeypatch.setattr("acme.customer.runtime.build_llm", lambda *a, **k: FakeLlm())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as c:
        assert (await c.get("/scenarios")).status_code == 404
        assert (await c.post("/scenarios/happy-path/start")).status_code == 404


# --- reads move time-triggered steps on (#395) -------------------------------------------------
async def _resolved_engine():
    """An engine with the happy-path cancelled and RESOLVED, the 6 min outreach not yet due."""
    from acme.api.scenarios import NOTIFY_AT  # noqa: F401

    app = engine()
    app.scenario_start("happy-path")
    await app.scenario_step()
    return app, app.scenario_state()["journey_id"]


def _types(events):
    return [e["type"] for e in events]


async def test_ac1_journey_events_fires_the_due_outreach_without_a_detail_read():
    from acme.api.scenarios import NOTIFY_AT

    app, jid = await _resolved_engine()
    assert "notification_sent" not in _types(await app.journey_events(jid))  # not due yet
    app.runtime.clock.advance(NOTIFY_AT - app.runtime.clock.now())
    assert _types(await app.journey_events(jid)).count("notification_sent") == 1
    assert _types(await app.journey_events(jid)).count("notification_sent") == 1  # once


async def test_ac1_journey_events_closes_a_no_offer_journey_after_the_window():
    from datetime import timedelta

    from acme.api.scenarios import NOTIFY_AT

    app, jid = await _resolved_engine()
    app.runtime.clock.advance(NOTIFY_AT - app.runtime.clock.now())
    await app.journey_events(jid)
    await app.runtime.handle_inbound("P-MARIANA", "Perfect, thanks!")
    app.runtime.clock.advance(app.runtime.offer_window + timedelta(minutes=1))
    events = _types(await app.journey_events(jid))
    assert events.count("service_closure") == 1
    assert app.runtime.journeys[jid].state.status.value == "CLOSED"


async def test_ac2_whatsapp_messages_delivers_the_due_outreach_without_a_detail_read():
    from acme.api.scenarios import NOTIFY_AT

    app, _ = await _resolved_engine()
    before = [m for m in await app.whatsapp_messages("P-MARIANA") if m["direction"] == "outbound"]
    app.runtime.clock.advance(NOTIFY_AT - app.runtime.clock.now())
    after = [m for m in await app.whatsapp_messages("P-MARIANA") if m["direction"] == "outbound"]
    assert len(after) == len(before) + 1 and "AC1240" in after[-1]["text"]


async def test_ac3_backend_polls_the_timeline_and_the_phone_past_t_plus_6_and_10():
    from datetime import timedelta

    from acme.api.engine_backend import EngineBackend
    from acme.api.scenarios import NOTIFY_AT
    from acme.engines.sales import build_sales_engine_app
    from tests.api.test_engine_mode import LocalEngine

    customer, jid = await _resolved_engine()
    sales = build_sales_engine_app(customer_engine=customer)
    sales.set_up()
    backend = EngineBackend(LocalEngine(customer), LocalEngine(sales))
    clock = customer.runtime.clock
    assert clock.now() < NOTIFY_AT
    clock.advance(NOTIFY_AT - clock.now())
    phone = await backend.api("whatsapp_messages", passenger_id="P-MARIANA")
    assert [m["direction"] for m in phone].count("outbound") >= 1
    assert _types(await backend.timeline(jid)).count("notification_sent") == 1
    await customer.runtime.handle_inbound("P-MARIANA", "Perfect, thanks!")
    clock.advance(customer.runtime.offer_window + timedelta(minutes=1))
    backend._timelines.clear()  # noqa: SLF001 - skip the poll-interval cache
    assert _types(await backend.timeline(jid)).count("service_closure") == 1


@pytest.mark.parametrize("sid", ["happy-path", "declines-upgrade"])
async def test_scenario_step_with_no_sent_offer_returns_409_error_as_data(metric_points, sid):
    """#455 AC2: CustomerEngineApp.scenario_step catches NoSentOffer and returns 409 no_offer_sent
    as data with the replay tape, without raising EngineOperationError or counting http_5xx."""
    app = engine()
    before = list(metric_points().get("acme.http.server.errors", []))
    app.scenario_start(sid)
    for _ in range(4):  # cancel, notify, after-10am, ack (without a Sales tick -> no offer sent)
        await app.scenario_step()
    out = await app.scenario_step()
    assert out["error"] == {
        "status": 409,
        "code": "no_offer_sent",
        "detail": "no offer has been sent to this passenger",
    }
    assert isinstance(out["tape"], list) and len(out["tape"]) >= 2
    assert app.scenarios.index == 4
    assert list(metric_points().get("acme.http.server.errors", [])) == before


async def test_journey_detail_op_carries_bag_routing_transfers_and_needs_human():  # #477
    app = engine()
    app.scenario_start("happy-path")
    await app.scenario_step()
    (jid,) = [j["journey_id"] for j in app.journeys_list()]
    d = await app.journey_detail(jid)
    assert d["bags"][0]["routing"] == "AC1240" and d["needs_human"] is False
    assert [t["leg"] for t in d["transfers"]] == ["airport_to_hotel", "hotel_to_airport"]
    from acme.api.app import JourneyDetail

    assert JourneyDetail(**d).transfers[0].origin == "GRU"  # what EngineBackend deserialises
