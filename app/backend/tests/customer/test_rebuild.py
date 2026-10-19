import pytest

from acme.customer.runtime import CustomerRuntime
from acme.customer.specialists.seat import nearest_window_seat
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus as S
from acme.sim.dcs import dcs_state


@pytest.fixture
def rt():
    return CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), auto_duty_of_care=False)


def ev(rt, jid, type_):
    return [e for e in rt.journeys[jid].state.timeline if e.type == type_]


async def run(rt):
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    return jid


async def test_rebuild_rebooks_seats_and_routes_bag(rt):
    jid = await run(rt)
    pnr = rt.world.pnrs["MSLV42"]
    assert [s.flight_number for s in pnr.segments] == ["AC1240", "AC0800"]
    assert [(s.flight_number, s.seat) for s in pnr.seats] == [("AC1240", "14A")]
    assert dcs_state(rt.sim).bag("BAG0001")["routing"] == "AC1240"
    (rebooked,) = ev(rt, jid, "rebooked")
    assert rebooked.payload["flight"] == "AC1240"
    assert rebooked.payload["departure"].startswith("2026-10-10T08:15")
    (held,) = ev(rt, jid, "seat_held")
    assert (held.payload["flight"], held.payload["seat"]) == ("AC1240", "14A")
    (bag,) = ev(rt, jid, "bag_rerouted")
    assert (bag.payload["flight"], bag.payload["tag"]) == ("AC1240", "BAG0001")
    (check,) = ev(rt, jid, "connection_verified")
    assert check.payload["feasible"] and check.payload["margin_minutes"] == 135
    assert (check.payload["flight"], check.payload["connection_flight"]) == ("AC1240", "AC0800")
    assert rt.journeys[jid].state.status == S.RESOLVING and not ev(rt, jid, "needs_human")


async def test_taken_seat_gets_nearest_window_with_explanation(rt):
    rt.world.flights["AC1240"].open_seats["economy"] = ["15F", "16A", "20A", "14B"]
    jid = await run(rt)
    (held,) = ev(rt, jid, "seat_held")
    assert held.payload["seat"] == "15F"
    assert held.payload["requested"] == "14A"
    assert "14A" in held.payload["reason"] and "window" in held.payload["reason"]
    assert [s.seat for s in rt.world.pnrs["MSLV42"].seats] == ["15F"]


async def test_no_window_seat_needs_human_but_bag_still_routes(rt):
    rt.world.flights["AC1240"].open_seats["economy"] = ["14B"]
    jid = await run(rt)
    (nh,) = ev(rt, jid, "needs_human")
    assert nh.payload["specialist"] == "seat"
    assert ev(rt, jid, "bag_rerouted") and not ev(rt, jid, "seat_held")


async def test_no_feasible_flight_needs_human_and_stays_resolving(rt):
    for n in ("AC1240", "AC1252"):
        rt.world.flights[n].status = "cancelled"
    jid = await run(rt)
    (nh,) = ev(rt, jid, "needs_human")
    assert nh.payload["specialist"] == "irrops_rebooking"
    assert rt.journeys[jid].state.status == S.RESOLVING
    assert not ev(rt, jid, "rebooked") and not ev(rt, jid, "seat_held")
    assert [s.flight_number for s in rt.world.pnrs["MSLV42"].segments][0] == "AC1234"


async def test_picks_earliest_feasible_not_infeasible_ones(rt):
    rt.world.flights["AC1240"].status = "cancelled"
    jid = await run(rt)
    assert ev(rt, jid, "rebooked")[0].payload["flight"] == "AC1252"  # AC1260 is infeasible


async def test_no_onward_connection_takes_earliest_flight_and_skips_check(rt):
    rt.world.pnrs["MSLV42"].segments = rt.world.pnrs["MSLV42"].segments[:1]
    jid = await run(rt)
    assert ev(rt, jid, "rebooked")[0].payload["flight"] == "AC1240"
    assert not ev(rt, jid, "connection_verified")


@pytest.mark.parametrize(
    ("open_seats", "want", "expected"),
    [
        (["14A", "15A"], "14A", "14A"),
        (["15A", "13K"], "14A", "15A"),
        (["16A", "12F"], "14A", "16A"),
        (["14B", "14C"], "14A", None),
        ([], "14A", None),
        (["13A", "15A"], "14A", "13A"),
        (["14A", "15A"], "XX", None),
        (["15A"], "", None),
        (["15A"], "14", None),
        (["15A"], "14a", None),
    ],
)
def test_nearest_window_seat(open_seats, want, expected):
    assert nearest_window_seat(open_seats, want) == expected


async def test_malformed_seat_code_needs_human_instead_of_raising(rt):
    from acme.customer.specialists.seat import SeatRequest, SeatSpecialist

    emitted: list[tuple[str, dict]] = []
    specialist = SeatSpecialist(rt.client)
    req = SeatRequest(pnr="MSLV42", flight="AC1240", preferred_seat="??", tier="diamond")
    resp = await specialist.handle(req, lambda t, p=None: emitted.append((t, p or {})))
    assert resp.status == "unavailable"
    assert [t for t, _ in emitted] == ["needs_human"]


async def test_secondary_cancellation_reopens_resolved_or_closed_journey_and_ignores_duplicate():
    from uuid import uuid4

    from acme.domain.journey import InvalidTransition
    from acme.domain.models import Offer

    full_rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT))
    (jid,) = await full_rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    rec = full_rt.journeys[jid]
    assert rec.state.status == S.RESOLVED
    first_run_id = rec.run_id

    # Duplicate cancellation of the same already-cancelled flight is an idempotent no-op.
    assert await full_rt.trigger_cancellation("AC1234", "unscheduled maintenance") == []
    assert rec.run_id == first_run_id
    assert len(ev(full_rt, jid, "cancellation_detected")) == 1

    # Simulate a declined offer and CLOSED journey on AC1240, plus a live offer to verify
    # both settled-offer clearing and live-offer invalidation on reopen.
    declined = rec.state.add_offer(
        Offer(
            offer_id=uuid4(),
            journey_id=jid,
            flight="AC1240",
            cabin="premium_business",
            price="135.00",
            standard_price="290.00",
            net_margin="80.00",
            currency="USD",
            origin="sales",
            optional=True,
        )
    )
    declined.status = "declined"
    rec.state.resolution_acknowledged = True
    rec.state.transition(S.CLOSED, actor="customer_agent")
    live = rec.state.add_offer(declined.offer.model_copy(update={"offer_id": uuid4()}))
    live.status = "sent"

    # Secondary disruption: AC1240 is cancelled -> re-opens journey, rotates run_id, rebooks AC1252.
    assert await full_rt.trigger_cancellation("AC1240", "weather") == [jid]
    assert rec.run_id != first_run_id
    assert rec.state.status == S.RESOLVED
    assert rec.state.resolution_acknowledged is False
    assert live.status == "invalidated"
    assert [o.status for o in rec.state.offers] == ["invalidated"]
    assert [s.flight_number for s in full_rt.world.pnrs["MSLV42"].segments] == ["AC1252", "AC0800"]
    assert [e.payload["flight"] for e in ev(full_rt, jid, "cancellation_detected")] == [
        "AC1234",
        "AC1240",
    ]
    assert [e.payload["flight"] for e in ev(full_rt, jid, "rebooked")] == ["AC1240", "AC1252"]
    assert (
        ev(full_rt, jid, "duty_of_care_assessed")[-1]
        .payload["rule_reasons"]["compensation"]
        .startswith("CDC Art. 14")
    )
    assert len(rec.vouchers) == 2 and len(full_rt.sim.partners.vouchers) == 2
    assert len(full_rt.sim.partners.hotel_bookings) == 1
    assert len(full_rt.sim.partners.transfers) == 2

    # reopen_for_disruption from a non-RESOLVED/CLOSED state raises InvalidTransition.
    rec.state.transition(S.REPLANNING, actor="customer_agent")
    with pytest.raises(InvalidTransition):
        rec.state.reopen_for_disruption()


@pytest.mark.parametrize("branch", ["accept", "decline", "no_offer"])
async def test_secondary_cancellation_after_accepted_upgrade_voids_charge_and_verifies(branch):
    from datetime import timedelta
    from decimal import Decimal
    from uuid import uuid4

    from acme.customer.conversion import accept_offer, decline_offer
    from acme.customer.skills import CustomerSkills
    from acme.customer.verify import verify_journey

    full_rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT))
    full_rt.world.flights["AC1240"].open_seats["premium_business"] = ["2A"]
    full_rt.world.inventory.append(
        full_rt.world.inventory[0].model_copy(update={"flight_number": "AC1240"})
    )
    skills = CustomerSkills(full_rt)
    (jid,) = await full_rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    rec = full_rt.journeys[jid]
    full_rt.clock.advance(timedelta(minutes=7))
    await full_rt.tick()
    await full_rt.handle_inbound("P-MARIANA", "Perfect, thanks!")

    first_oid = str(uuid4())
    res1 = await skills.propose_and_deliver(
        {
            "offer_id": first_oid,
            "journey_id": jid,
            "flight": "AC1240",
            "cabin": "premium_business",
            "price": "135.00",
            "standard_price": "290.00",
            "net_margin": "80.00",
            "currency": "USD",
            "origin": "sales",
            "optional": True,
        }
    )
    assert res1["status"] == "approved"
    assert await accept_offer(full_rt, rec) == "accepted"
    assert rec.state.status == S.CLOSED
    assert full_rt.sim.partners.net_charged("MSLV42") == Decimal("135.00")

    # Secondary cancellation on AC1240 voids the Disruption 1 upgrade charge and reuses care.
    assert await full_rt.trigger_cancellation("AC1240", "weather") == [jid]
    assert full_rt.sim.partners.net_charged("MSLV42") == Decimal("0.00")
    assert len(rec.vouchers) == 2 and len(full_rt.sim.partners.vouchers) == 2
    assert len(full_rt.sim.partners.hotel_bookings) == 1
    assert len(full_rt.sim.partners.transfers) == 2
    voided = ev(full_rt, jid, "charge_voided")
    assert len(voided) == 1 and voided[-1].payload["offer_id"] == first_oid
    decisions = [
        e.payload
        for e in ev(full_rt, jid, "guardrail_decision")
        if e.payload.get("offer_id") == first_oid
    ]
    assert decisions[-1]["approved"] is False

    full_rt.clock.advance(timedelta(minutes=7))
    await full_rt.tick()
    await full_rt.handle_inbound("P-MARIANA", "Perfect, thanks!")

    if branch in ("accept", "decline"):
        res2 = await skills.propose_and_deliver(
            {
                "offer_id": str(uuid4()),
                "journey_id": jid,
                "flight": "AC1252",
                "cabin": "premium_business",
                "price": "135.00",
                "standard_price": "290.00",
                "net_margin": "80.00",
                "currency": "USD",
                "origin": "sales",
                "optional": True,
            }
        )
        assert res2["status"] == "approved"
        if branch == "accept":
            assert await accept_offer(full_rt, rec) == "accepted"
            assert full_rt.sim.partners.net_charged("MSLV42") == Decimal("135.00")
        else:
            assert await decline_offer(full_rt, rec) == "declined"
            assert full_rt.sim.partners.net_charged("MSLV42") == Decimal("0.00")
    else:
        full_rt.clock.advance(full_rt.offer_window + timedelta(minutes=1))
        await full_rt.tick()
        assert full_rt.sim.partners.net_charged("MSLV42") == Decimal("0.00")

    assert rec.state.status == S.CLOSED
    assert (await verify_journey(full_rt, rec)).ok is True


@pytest.mark.parametrize(
    ("tool", "specialist"), [("seat.assign", "seat"), ("bag.reroute", "baggage")]
)
async def test_specialist_tool_error_needs_human_instead_of_raising(
    rt, monkeypatch, tool, specialist
):
    from acme.sim.client import SimClient, ToolError

    real = SimClient.call

    async def flaky(self, server, name, **kw):
        if name == tool:
            raise ToolError(f"{name} down")
        return await real(self, server, name, **kw)

    monkeypatch.setattr(SimClient, "call", flaky)
    jid = await run(rt)
    (nh,) = ev(rt, jid, "needs_human")
    assert nh.payload["specialist"] == specialist
    assert rt.journeys[jid].state.status == S.RESOLVING
