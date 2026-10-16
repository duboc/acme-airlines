"""Scenario 4b: guardrailed offer + delivery with a time-of-use re-check (#15, #83)."""

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from acme.api.events import TimelineEvent
from acme.customer.runtime import CustomerRuntime
from acme.customer.skills import CustomerSkills, SkillError
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus as S
from acme.llm import FakeLlm


class SpyLlm(FakeLlm):
    def __init__(self):
        super().__init__()
        self.contexts = []

    async def compose_passenger_message(self, context):
        self.contexts.append(dict(context))
        return await super().compose_passenger_message(context)


def offer(jid, **over):
    base = dict(
        offer_id=str(uuid4()), journey_id=jid, flight="AC1252", cabin="premium_business",
        price="135.00", standard_price="290.00", net_margin="80.00", currency="USD",
        origin="sales", optional=True,
    )  # fmt: skip
    return {**base, **over}


@pytest.fixture
async def ctx():
    llm = SpyLlm()
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), auto_duty_of_care=False, llm=llm)
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    await rt.sim.rebook("MSLV42", "AC1252")
    state = rt.journeys[jid].state
    state.transition(S.RESOLVED)
    state.resolution_acknowledged = True
    return type("C", (), {"rt": rt, "jid": jid, "state": state, "llm": llm,
                          "skills": CustomerSkills(rt)})  # fmt: skip


def offer_messages(c):
    return [m for m in c.rt.sim.partners.transcripts.get("P-MARIANA", []) if m["buttons"]]


async def test_approved_offer_is_sent_once_optional_with_buttons(ctx):
    out = await ctx.skills.propose_and_deliver(offer(ctx.jid))
    assert out["status"] == "approved" and set(out) == {"status", "offer_id", "reasons"}
    (msg,) = offer_messages(ctx)
    assert msg["buttons"] == ["Accept", "No thanks"]
    assert {"boarding_pass", "hotel_voucher"} <= set(msg["attachments"])
    assert "optional" in msg["body"].lower() and "$135.00" in msg["body"]
    assert "AC1252" in msg["body"] and "premium business" in msg["body"].lower()
    (rec,) = ctx.state.offers
    assert rec.status == "sent"
    types = [e.type for e in ctx.state.timeline]
    assert "guardrail_decision" in types and "offer_sent" in types


async def test_message_and_llm_input_contain_no_sales_authored_values(ctx):
    sales = offer(ctx.jid, seat_hint="9K")
    await ctx.skills.propose_and_deliver(sales)
    (msg,) = offer_messages(ctx)
    haystack = msg["body"] + json.dumps(ctx.llm.contexts, default=str)
    for value in (sales["offer_id"], sales["journey_id"], "9K", "sales"):
        assert value not in haystack
    assert "2A" in msg["body"]  # seat comes from the Customer's own inventory data
    assert Decimal("135.00") == ctx.state.offers[0].offer.price  # only price is the offer's


async def test_second_proposal_after_sent_or_declined_is_rejected_and_silent(ctx):
    await ctx.skills.propose_and_deliver(offer(ctx.jid))
    again = await ctx.skills.propose_and_deliver(offer(ctx.jid))
    assert again["status"] == "rejected" and "offer_already_made" in again["reasons"]
    ctx.state.offers[0].status = "declined"
    third = await ctx.skills.propose_and_deliver(offer(ctx.jid))
    assert "offer_already_made" in third["reasons"]
    assert len(offer_messages(ctx)) == 1


async def test_concurrent_proposals_send_at_most_one_message(ctx):
    results = await asyncio.gather(
        *[ctx.skills.propose_and_deliver(offer(ctx.jid)) for _ in range(5)]
    )
    assert sorted(r["status"] for r in results) == ["approved"] + ["rejected"] * 4
    assert len(offer_messages(ctx)) == 1
    assert [r.status for r in ctx.state.offers].count("sent") == 1


@pytest.mark.parametrize("status", [S.REPLANNING, S.RESOLVING])
async def test_proposal_outside_resolved_is_rejected_not_recorded_and_silent(ctx, status):
    if status is S.REPLANNING:
        ctx.state.transition(S.REPLANNING)
    else:
        ctx.state.status = S.RESOLVING  # forced: attack the handler directly
    with pytest.raises(SkillError, match="disruption_not_resolved"):
        await ctx.skills.propose_and_deliver(offer(ctx.jid))
    with pytest.raises(SkillError):
        ctx.skills.propose_offer(offer(ctx.jid))  # the synchronous entry point too (#83)
    assert ctx.state.offers == [] and offer_messages(ctx) == []
    decisions = [e for e in ctx.state.timeline if e.type == "guardrail_decision"]
    assert decisions and not decisions[-1].payload["approved"]
    assert "disruption_not_resolved" in decisions[-1].payload["reasons"]


async def test_journey_leaving_resolved_before_send_invalidates_the_offer(ctx, monkeypatch):
    real = ctx.rt.client.call
    inventory_calls = 0

    async def call(server, tool, **args):
        nonlocal inventory_calls
        if server == "inventory" and tool == "cabin_inventory.get":
            inventory_calls += 1
            if inventory_calls == 2:  # the send-time re-check fetch: the passenger replans now
                ctx.state.transition(S.REPLANNING)
        return await real(server, tool, **args)

    monkeypatch.setattr(ctx.rt.client, "call", call)
    out = await ctx.skills.propose_and_deliver(offer(ctx.jid))
    assert out["status"] == "rejected" and "disruption_not_resolved" in out["reasons"]
    assert offer_messages(ctx) == []
    assert [r.status for r in ctx.state.offers] == ["invalidated"]


async def test_tier_comes_from_the_loyalty_system_not_from_cached_state(ctx):
    ctx.state.passenger_tier = "diamond"  # stale/forged local value
    ctx.rt.world.passengers["P-MARIANA"].tier = "silver"  # loyalty says silver
    out = await ctx.skills.propose_and_deliver(offer(ctx.jid))
    assert out["status"] == "rejected" and "tier_not_eligible" in out["reasons"]
    assert offer_messages(ctx) == []


@pytest.mark.parametrize(
    "over, reason",
    [
        ({"price": "200.00", "net_margin": "145.00"}, "not_a_genuine_discount"),
        ({"standard_price": "990.00"}, "price_claim_mismatch"),
        ({"price": "40.00", "net_margin": "80.00"}, "negative_margin"),
    ],
)
async def test_guardrail_rejections_return_codes_only_and_stay_silent(ctx, over, reason):
    out = await ctx.skills.propose_and_deliver(offer(ctx.jid, **over))
    assert out["status"] == "rejected" and reason in out["reasons"]
    assert offer_messages(ctx) == []
    assert [r.status for r in ctx.state.offers] == ["rejected"]


async def test_no_free_text_leaks_in_the_sales_response(ctx):
    out = await ctx.skills.propose_and_deliver(offer(ctx.jid, price="200.00", net_margin="145.00"))
    assert all(r.replace("_", "").isalpha() for r in out["reasons"])


async def test_flight_other_than_the_journeys_is_rejected(ctx):
    out = await ctx.skills.propose_and_deliver(offer(ctx.jid, flight="AC1240"))
    assert "flight_mismatch" in out["reasons"] and offer_messages(ctx) == []


async def test_a2a_surface_returns_decision_or_error(ctx):
    from tests.customer.test_a2a_skills import a2a

    ok = await ctx.skills.handle_a2a(a2a("propose_offer", {"offer": offer(ctx.jid)}))
    assert ok["result"]["parts"][0]["data"]["status"] == "approved"
    ctx.state.transition(S.REPLANNING)
    err = await ctx.skills.handle_a2a(a2a("propose_offer", {"offer": offer(ctx.jid)}))
    assert "disruption_not_resolved" in err["error"]["message"]


async def test_events_conform_to_the_typed_union(ctx):
    await ctx.skills.propose_and_deliver(offer(ctx.jid))
    await ctx.skills.propose_and_deliver(offer(ctx.jid))
    adapter = TypeAdapter(TimelineEvent)
    for e in ctx.state.timeline:
        adapter.validate_python(e.model_dump(mode="json"))


async def test_api_exposes_the_sent_offer_and_guardrail_decisions():
    import httpx

    from acme.api.app import create_app

    app = create_app(FixedClock(DISRUPTION_DETECTED_AT))
    rt = app.state.runtime
    rt.auto_duty_of_care = False
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    await rt.sim.rebook("MSLV42", "AC1252")
    rt.journeys[jid].state.transition(S.RESOLVED)
    rt.journeys[jid].state.resolution_acknowledged = True
    await CustomerSkills(rt).propose_and_deliver(offer(jid))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        d = (await c.get(f"/journeys/{jid}")).json()
        sse = await c.get(f"/journeys/{jid}/events?limit={len(rt.journeys[jid].state.timeline)}")
    assert d["offer"]["status"] == "sent" and d["offer"]["price"] == "135.00"
    assert "guardrail_decision" in sse.text and "offer_sent" in sse.text


async def test_offer_compose_accepts_capitalized_word_tokens_and_rejects_any_missing_fact(
    ctx, metric_points
):
    from acme.domain.models import Offer
    from tests.customer.test_notification import fallback_count

    class ReplyLlm(FakeLlm):
        def __init__(self, reply: str):
            super().__init__()
            self.reply = reply

        async def compose_passenger_message(self, context):
            return self.reply

    rec = ctx.rt.journeys[ctx.jid]
    rec.state.current_flight = "AC1252"
    inv = ctx.rt.sim.cabin_inventory("AC1252", "premium_business")
    mod_offer = Offer.model_validate(offer(ctx.jid))

    valid_caps = "Optional upgrade to Premium Business on AC1252 for $135.00."
    ctx.rt.llm = ReplyLlm(valid_caps)
    before = fallback_count(metric_points, "offer")
    text, _ = await ctx.skills._compose(rec, mod_offer, inv)  # noqa: SLF001
    assert text == valid_caps
    assert fallback_count(metric_points, "offer") - before == 0

    for omitted in ("AC1252", "$135.00", "Optional", "Premium Business"):
        ctx.rt.llm = ReplyLlm(valid_caps.replace(omitted, "XXX"))
        b = fallback_count(metric_points, "offer")
        fallback_text, _ = await ctx.skills._compose(rec, mod_offer, inv)  # noqa: SLF001
        assert fallback_text != valid_caps.replace(omitted, "XXX")
        assert fallback_count(metric_points, "offer") - b == 1, omitted
