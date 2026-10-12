"""Spans cover specialists, guardrail decisions, A2A skills and Sales pitches, and carry no PII."""

import json
import logging

import pytest

from acme.domain.fixtures import mariana_world
from tests.governance.flow import Flow, offer_payload


@pytest.fixture
async def flow():
    f = Flow()
    yield f
    await f.http.aclose()


def pii_values(flow: Flow) -> set[str]:
    """Everything personal in the fixtures plus the text actually sent or received."""
    world = mariana_world()
    values: set[str] = set()
    for p in world.passengers.values():
        values |= {p.name, *p.name.split()}
        values |= {v for v in (p.phone, p.loyalty.member_id if p.loyalty else None) if v}
    values |= set(world.pnrs)
    for transcript in flow.rt.sim.partners.transcripts.values():
        values |= {m["body"] for m in transcript}
    values.add("Can I leave after 10am?")
    values.add("Perfect, thanks!")
    return {v for v in values if v}


async def run_story(flow: Flow) -> None:
    await flow.acknowledged_replan()
    # a rejected proposal over A2A: the guardrail decides, reason codes only
    await flow.skills.handle_a2a(
        {
            "jsonrpc": "2.0", "id": 9, "method": "message/send",
            "params": {"message": {
                "messageId": "m", "role": "user",
                "parts": [{"kind": "data", "data": {
                    "skill": "propose_offer",
                    "args": {"offer": offer_payload(flow.jid, price="289.00")},
                }}],
            }},
        }
    )  # fmt: skip


async def test_spans_cover_the_story_and_carry_the_journey_id(flow, spans):
    await run_story(flow)
    finished = spans.get_finished_spans()
    names = {s.name for s in finished}
    assert {
        "acme.customer.specialist.irrops_rebooking",
        "acme.customer.specialist.seat",
        "acme.customer.specialist.baggage",
        "acme.customer.specialist.rights",
        "acme.customer.specialist.change",
        "acme.guardrail.decision",
        "acme.a2a.list_journeys",
        "acme.a2a.journey_status",
        "acme.a2a.propose_offer",
        "acme.sales.pitch",
    } <= names
    decisions = [s for s in finished if s.name == "acme.guardrail.decision"]
    assert {s.attributes["acme.guardrail.decision"] for s in decisions} == {"approve", "reject"}
    assert all(s.attributes.get("acme.guardrail.reason_code") for s in decisions)
    for s in finished:
        if s.name not in {"acme.a2a.list_journeys"}:
            assert s.attributes.get("acme.journey_id") == flow.jid, s.name


async def test_no_pii_in_span_attributes_or_logs(flow, spans, caplog):
    # acme at DEBUG; third-party DEBUG (mcp wire dumps) is outside our instrumented paths
    caplog.set_level(logging.DEBUG, logger="acme")
    caplog.set_level(logging.INFO)
    await run_story(flow)
    pii = pii_values(flow)
    assert "MSLV42" in pii and "Mariana Silva" in pii
    assert spans.get_finished_spans()
    attrs = json.dumps(
        [dict(s.attributes) | {"name": s.name} for s in spans.get_finished_spans()]
        + [e.attributes for s in spans.get_finished_spans() for e in s.events],
        default=str,
    )
    logs = caplog.text + " ".join(r.getMessage() for r in caplog.records)
    for value in pii:
        assert value not in attrs, f"PII in span data: {value[:3]}..."
        assert value not in logs, f"PII in logs: {value[:3]}..."
