"""Time-of-check / time-of-use races. Each test injects the passenger or system action at an
exact await point (see `Flow.interleave`); money must never move on a stale approval."""

import asyncio
from decimal import Decimal

import pytest

from acme.customer.conversion import accept_offer
from acme.customer.skills import SkillError
from acme.domain.journey import DisruptionStatus as S
from acme.sim.client import ToolError
from tests.governance.flow import Flow, offer_payload


@pytest.fixture
async def flow():
    f = Flow()
    yield f
    await f.http.aclose()


async def resolved_and_acknowledged(flow: Flow) -> None:
    """Replanned onto AC1252 and acknowledged, but no radar tick yet (no offer exists)."""
    await flow.cancel()
    await flow.notify()
    await flow.rt.handle_inbound("P-MARIANA", "Can I leave after 10am?")
    await flow.rt.handle_inbound("P-MARIANA", "Perfect, thanks!")
    assert flow.state.status is S.RESOLVED and flow.state.resolution_acknowledged


def no_money_moved(flow: Flow) -> None:
    assert flow.charges() == {} or flow.rt.sim.partners.net_charged("MSLV42") == Decimal("0")
    assert flow.seat() == "14A"


# (a) Sales proposes -> the passenger asks for a change before evaluation
async def test_a_change_request_during_proposal_evaluation_rejects_the_offer(flow):
    await resolved_and_acknowledged(flow)
    flow.interleave("loyalty", "member.get", flow.to_replanning)
    try:
        out = await flow.skills.propose_and_deliver(offer_payload(flow.jid))
        assert out["status"] != "approved"
    except SkillError:
        pass  # rejected outright: also correct
    assert flow.offer_messages() == []
    assert all(r.status != "sent" for r in flow.state.offers)


# (b) approved -> REPLANNING before send
async def test_replanning_between_approval_and_send_blocks_the_send(flow):
    await resolved_and_acknowledged(flow)
    flow.interleave("crm", "profile.get", flow.to_replanning)  # during delivery composition
    out = await flow.skills.propose_and_deliver(offer_payload(flow.jid))
    assert out["status"] == "rejected" and "disruption_not_resolved" in out["reasons"]
    assert flow.offer_messages() == []
    assert [r.status for r in flow.state.offers] == ["invalidated"]


# (c) sent -> REPLANNING -> Accept
async def test_accept_after_replanning_moves_no_money_and_tells_the_truth(flow):
    await flow.sent_offer_ready()
    flow.to_replanning()
    before = len(flow.outbound())
    assert await accept_offer(flow.rt, flow.rec) == "invalidated"
    no_money_moved(flow)
    assert flow.charges() == {}
    assert len(flow.outbound()) == before + 1
    last = flow.outbound()[-1]["body"]
    assert "no longer available" in last and "not been charged" in last
    assert "keep your current seat" in last


# (d) duplicate Accept
async def test_duplicate_accepts_charge_exactly_once(flow):
    await flow.sent_offer_ready()
    results = await asyncio.gather(*[accept_offer(flow.rt, flow.rec) for _ in range(6)])
    assert set(results) == {"accepted"}
    assert len(flow.charges()) == 1
    assert flow.rt.sim.partners.net_charged("MSLV42") == Decimal("135.00")


# (e) 2A taken / inventory gone before or during Accept
async def test_accept_when_2a_was_taken_earlier_charges_nothing(flow):
    await flow.sent_offer_ready()
    flow.other_pnr_takes("2A")
    assert await accept_offer(flow.rt, flow.rec) == "invalidated"
    no_money_moved(flow)
    assert "not been charged" in flow.outbound()[-1]["body"]


async def test_accept_when_inventory_hits_zero_charges_nothing(flow):
    await flow.sent_offer_ready()
    flow.rt.world.inventory[0].open_seats = 0
    assert await accept_offer(flow.rt, flow.rec) == "invalidated"
    no_money_moved(flow)
    assert "no longer available" in flow.outbound()[-1]["body"]


async def test_2a_taken_between_the_check_and_the_hold_charges_nothing(flow):
    await flow.sent_offer_ready()
    flow.interleave("dcs", "seat.hold", lambda: flow.other_pnr_takes("2A"))
    assert await accept_offer(flow.rt, flow.rec) == "invalidated"
    no_money_moved(flow)


async def test_a_failure_after_the_charge_leaves_no_net_charge_and_seat_14a(flow):
    await flow.sent_offer_ready()

    def boom():
        raise ToolError("dcs/seat.assign: injected failure")

    flow.interleave("dcs", "seat.assign", boom)
    assert await accept_offer(flow.rt, flow.rec) == "invalidated"
    assert flow.rt.sim.partners.net_charged("MSLV42") == Decimal("0")
    assert flow.seat() == "14A"
    assert "payment was reversed" in flow.outbound()[-1]["body"]


@pytest.mark.xfail(strict=True, reason="bug #88: accept checks once, then holds/charges unguarded")
async def test_replanning_between_the_accept_check_and_the_charge_charges_nothing(flow):
    await flow.sent_offer_ready()
    flow.interleave("payments", "charge", flow.to_replanning)
    await accept_offer(flow.rt, flow.rec)
    assert flow.charges() == {} or flow.rt.sim.partners.net_charged("MSLV42") == Decimal("0")
    assert flow.seat() == "14A"


# (f) concurrent proposals for one journey
@pytest.mark.parametrize("n", [2, 5])
async def test_concurrent_proposals_yield_at_most_one_offer_message(flow, n):
    await resolved_and_acknowledged(flow)
    outs = await asyncio.gather(
        *[flow.skills.propose_and_deliver(offer_payload(flow.jid)) for _ in range(n)],
        return_exceptions=True,
    )
    assert len(flow.offer_messages()) == 1
    assert sum(1 for o in outs if isinstance(o, dict) and o["status"] == "approved") == 1
    assert [r.status for r in flow.state.offers].count("sent") == 1


async def test_concurrent_radar_ticks_and_proposals_yield_one_offer_message(flow):
    await resolved_and_acknowledged(flow)
    await asyncio.gather(
        flow.radar.tick(),
        flow.radar.tick(),
        flow.skills.propose_and_deliver(offer_payload(flow.jid)),
    )
    assert len(flow.offer_messages()) == 1
