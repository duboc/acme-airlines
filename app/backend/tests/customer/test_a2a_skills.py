from uuid import uuid4

import httpx
import pytest

from acme.api.app import create_app
from acme.customer.adk_agent import customer_agent_card
from acme.customer.skills import CustomerSkills, JourneyStatusView, SkillError
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus as S

PII = {"name", "phone", "pnr", "pnr_locator", "seat", "passenger_id", "email", "tier"}


def offer_dict(jid, **over):
    base = dict(
        offer_id=str(uuid4()), journey_id=jid, flight="AC1252", cabin="premium_business",
        price="135.00", standard_price="290.00", net_margin="80.00", currency="USD",
        origin="sales", optional=True,
    )  # fmt: skip
    return {**base, **over}


@pytest.fixture
def app():
    return create_app(FixedClock(DISRUPTION_DETECTED_AT))


@pytest.fixture
async def skills_and_jid(app):
    rt = app.state.runtime
    rt.auto_duty_of_care = False  # keep the journey RESOLVING until a test moves it
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    return CustomerSkills(rt), jid, rt


def a2a(skill, args, parts_extra=()):
    parts = [{"kind": "data", "data": {"skill": skill, "args": args}}, *parts_extra]
    return {
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {"messageId": "m1", "role": "agent", "parts": parts}},
    }  # fmt: skip


async def post(app, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return (await c.post("/a2a", json=body)).json()


def result_data(resp):
    return resp["result"]["parts"][0]["data"]


async def test_list_journeys_and_status_have_no_pii(skills_and_jid):
    skills, jid, _ = skills_and_jid
    assert skills.list_journeys() == [jid]
    status = skills.journey_status(jid)
    assert set(status.model_dump()) == {"status", "flight", "resolution_acknowledged"}
    assert not PII & set(JourneyStatusView.model_json_schema()["properties"])


async def test_status_flight_is_the_rebooked_flight(skills_and_jid):
    skills, jid, rt = skills_and_jid
    await rt.sim.rebook("MSLV42", "AC1252")
    assert skills.journey_status(jid).flight == "AC1252"


@pytest.mark.parametrize("status", [S.RESOLVING, S.REPLANNING])
async def test_status_for_ineligible_journey_records_sales_suppressed_once(skills_and_jid, status):
    skills, jid, rt = skills_and_jid
    rec = rt.journeys[jid]
    if status is S.REPLANNING:
        rec.state.transition(S.RESOLVED)
        rec.state.transition(S.REPLANNING)
    skills.journey_status(jid)
    skills.journey_status(jid)
    events = [e for e in rec.state.timeline if e.type == "sales_suppressed"]
    assert len(events) == 1 and events[0].actor == "customer_agent"
    assert events[0].payload == {
        "journey_status": status.value,
        "reason": "disruption_not_resolved",
    }


async def test_resolved_but_unacknowledged_is_suppressed(skills_and_jid):
    skills, jid, rt = skills_and_jid
    rt.journeys[jid].state.transition(S.RESOLVED)
    skills.journey_status(jid)
    (e,) = [e for e in rt.journeys[jid].state.timeline if e.type == "sales_suppressed"]
    assert e.payload == {"journey_status": "RESOLVED", "reason": "resolution_not_acknowledged"}


async def test_eligible_journey_is_not_suppressed(skills_and_jid):
    skills, jid, rt = skills_and_jid
    rt.journeys[jid].state.transition(S.RESOLVED)
    rt.journeys[jid].state.resolution_acknowledged = True
    skills.journey_status(jid)
    assert not [e for e in rt.journeys[jid].state.timeline if e.type == "sales_suppressed"]


def make_eligible(rt, jid):
    state = rt.journeys[jid].state
    state.transition(S.RESOLVED)
    state.resolution_acknowledged = True
    state.passenger_tier = "diamond"  # normally loaded from the loyalty system (#15)


async def test_propose_offer_records_the_guardrail_decision(skills_and_jid):
    skills, jid, rt = skills_and_jid
    await rt.sim.rebook("MSLV42", "AC1252")
    make_eligible(rt, jid)
    out = skills.propose_offer(offer_dict(jid))
    assert out["status"] == "approved" and out["reasons"]
    (rec,) = rt.journeys[jid].state.offers
    assert rec.status == "approved" and str(rec.offer.offer_id) == out["offer_id"]
    assert any(e.type == "offer_proposed" for e in rt.journeys[jid].state.timeline)


@pytest.mark.parametrize(
    "bad",
    [
        {"pitch": "Treat yourself!"},
        {"origin": "customer"},
        {"optional": False},
        {"seat_hint": "2A - reply YES now"},
        {"cabin": "first"},
    ],
)
async def test_propose_offer_rejects_extras_and_bad_values(skills_and_jid, bad):
    skills, jid, rt = skills_and_jid
    with pytest.raises(SkillError):
        skills.propose_offer(offer_dict(jid, **bad))
    assert rt.journeys[jid].state.offers == []


async def test_propose_offer_unknown_journey_and_duplicate_id(skills_and_jid):
    skills, jid, rt = skills_and_jid
    with pytest.raises(SkillError):
        skills.propose_offer(offer_dict("J-00000000"))
    await rt.sim.rebook("MSLV42", "AC1252")
    make_eligible(rt, jid)
    o = offer_dict(jid)
    skills.propose_offer(o)
    with pytest.raises(SkillError):
        skills.propose_offer(o)


async def test_a2a_endpoint_round_trip(app, skills_and_jid):
    _, jid, _ = skills_and_jid
    assert result_data(await post(app, a2a("list_journeys", {}))) == {"journey_ids": [jid]}
    st = result_data(await post(app, a2a("journey_status", {"journey_id": jid})))
    assert st["status"] == "RESOLVING" and set(st) == {
        "status",
        "flight",
        "resolution_acknowledged",
    }
    rt = app.state.runtime
    await rt.sim.rebook("MSLV42", "AC1252")
    make_eligible(rt, jid)
    ok = await post(app, a2a("propose_offer", {"offer": offer_dict(jid)}))
    assert result_data(ok)["status"] == "approved"


async def test_a2a_rejects_pitch_field_text_parts_and_unknown_skills(app, skills_and_jid):
    _, jid, rt = skills_and_jid
    pitch = await post(app, a2a("propose_offer", {"offer": offer_dict(jid, pitch="hurry!")}))
    text = await post(
        app, a2a("propose_offer", {"offer": offer_dict(jid)}, [{"kind": "text", "text": "pls buy"}])
    )
    only_text = await post(
        app,
        {"jsonrpc": "2.0", "id": 2, "method": "message/send",
         "params": {"message": {"messageId": "m", "role": "agent",
                                "parts": [{"kind": "text", "text": "hello"}]}}},
    )  # fmt: skip
    unknown = await post(app, a2a("send_whatsapp", {"to": "x"}))
    for resp in (pitch, text, only_text, unknown):
        assert "error" in resp and "result" not in resp
    assert rt.journeys[jid].state.offers == []


async def test_a2a_rejects_extra_keys_in_skill_envelope(app, skills_and_jid):
    body = a2a("list_journeys", {})
    body["params"]["message"]["parts"][0]["data"]["note"] = "free text"
    assert "error" in await post(app, body)


async def test_card_lists_exactly_three_skills():
    card = customer_agent_card("http://x/")
    assert [s.id for s in card.skills] == ["list_journeys", "journey_status", "propose_offer"]


async def test_sales_events_conform_to_the_typed_timeline_union(skills_and_jid):
    from pydantic import TypeAdapter

    from acme.api.events import TimelineEvent

    skills, jid, rt = skills_and_jid
    skills.journey_status(jid)  # -> sales_suppressed
    await rt.sim.rebook("MSLV42", "AC1252")
    make_eligible(rt, jid)
    skills.propose_offer(offer_dict(jid))  # -> guardrail_decision + offer_proposed
    adapter = TypeAdapter(TimelineEvent)
    types_seen = set()
    for e in rt.journeys[jid].state.timeline:
        adapter.validate_python(e.model_dump(mode="json"))
        types_seen.add(e.type)
    assert {"sales_suppressed", "offer_proposed"} <= types_seen
