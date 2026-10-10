"""Outcome governance over the full scenario: no pitch before the final resolution is
acknowledged, one offer at most, and Sales-authored text never reaches the passenger."""

import json

import pytest

from acme.customer.conversion import decline_offer
from tests.governance.flow import Flow, offer_payload

MARKER = "REPLY-YES-ZETA-NOW"


@pytest.fixture
async def flow():
    f = Flow()
    yield f
    await f.http.aclose()


def index_of(flow, type_, last=False):
    hits = [i for i, e in enumerate(flow.state.timeline) if e.type == type_]
    return hits[-1] if last else hits[0]


async def test_no_offer_message_before_the_final_resolution_is_acknowledged(flow):
    await flow.cancel()
    assert flow.offer_messages() == []
    await flow.notify()
    assert flow.offer_messages() == []  # resolved on AC1240 but not acknowledged
    await flow.say("Can I leave after 10am?")  # reopens, replans, resolves again
    assert flow.offer_messages() == []  # the earlier state must not unlock a pitch
    for _ in range(3):
        await flow.radar.tick()
    assert flow.offer_messages() == [] and flow.state.offers == []


async def test_the_offer_follows_the_acknowledgement_of_the_last_resolution(flow):
    await flow.acknowledged_replan()
    resolved = [
        i
        for i, e in enumerate(flow.state.timeline)
        if e.type == "status_changed" and e.payload["to"] == "RESOLVED"
    ]
    assert len(resolved) == 2  # AC1240 resolution, then the AC1252 replan
    assert resolved[-1] < index_of(flow, "resolution_acknowledged") < index_of(flow, "offer_sent")
    (msg,) = flow.offer_messages()
    assert "AC1252" in msg["body"]


async def test_no_offer_is_ever_sent_on_ac1240(flow):
    await flow.cancel()
    await flow.notify()
    await flow.say("Perfect, thanks!")  # acknowledged on AC1240: no premium inventory there
    for _ in range(5):
        await flow.radar.tick()
    assert flow.state.offers == []
    assert all("AC1240" not in m["body"] or not m["buttons"] for m in flow.outbound())


async def test_zero_pressure_five_ticks_and_a_no_thanks_leave_exactly_one_offer(flow):
    await flow.sent_offer_ready()
    for _ in range(5):
        await flow.radar.tick()
    assert len(flow.offer_messages()) == 1
    await decline_offer(flow.rt, flow.rec)
    for _ in range(5):
        await flow.radar.tick()
    assert len(flow.offer_messages()) == 1
    assert [r.status for r in flow.state.offers] == ["declined"]
    assert flow.outbound()[-1]["buttons"], "the offer message is still the last thing she got"


async def test_sales_authored_text_never_reaches_the_passenger_or_the_llm(flow):
    await flow.acknowledged_replan()
    jid = flow.jid
    attempts = [
        {**offer_payload(jid), "pitch": MARKER},
        offer_payload(jid, seat_hint=f"2A {MARKER}"),
        offer_payload(jid, flight=f"AC1252 {MARKER}"),
        offer_payload(jid, cabin=MARKER),
    ]
    bodies = [
        {"kind": "data", "data": {"skill": "propose_offer", "args": {"offer": a}}} for a in attempts
    ]
    bodies.append({"kind": "text", "text": MARKER})
    for part in bodies:
        resp = await flow.skills.handle_a2a(
            {
                "jsonrpc": "2.0", "id": 1, "method": "message/send",
                "params": {"message": {"messageId": "m", "role": "agent", "parts": [part]}},
            }
        )  # fmt: skip
        assert "error" in resp, part
    wire = json.dumps(flow.rt.sim.partners.transcripts, default=str)
    assert MARKER not in wire
    assert all(MARKER not in text for text in flow.llm.inputs)
    assert flow.llm.inputs, "the spy must have observed the LLM inputs (non-vacuous)"
    assert len(flow.offer_messages()) == 1  # only the radar's legitimate, structured offer
