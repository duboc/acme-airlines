import httpx
import pytest

from acme.api.app import create_app
from acme.customer.runtime import CustomerRuntime
from acme.customer.specialists import RebuildResult, run_duty_of_care
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus as S

DONE = RebuildResult(new_flight="AC1240", seat_done=True, bag_done=True)


@pytest.fixture
async def setup():
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), auto_duty_of_care=False)
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    return rt, rt.journeys[jid]


def events(rec, type_):
    return [e for e in rec.state.timeline if e.type == type_]


async def test_assessment_records_entitlements_with_reasons(setup):
    rt, rec = setup
    await run_duty_of_care(rt, rec, DONE)
    (a,) = events(rec, "duty_of_care_assessed")
    p = a.payload
    assert p["hotel_required"] and p["ground_transfer_required"]
    assert p["meal_vouchers"] == ["dinner", "breakfast"]
    assert p["rule_reasons"]["hotel"].startswith("ANAC 400 Art. 27")
    assert p["compensation_eligible"] is True


async def test_books_hotel_transfers_and_vouchers_each_linked_to_reason(setup):
    rt, rec = setup
    await run_duty_of_care(rt, rec, DONE)
    pm = rt.sim.partners
    (hotel,) = events(rec, "hotel_booked")
    assert (
        hotel.payload["hotel_id"] == "HT8842" and hotel.payload["booking_ref"] in pm.hotel_bookings
    )
    assert hotel.payload["entitlement"] == "hotel" and hotel.payload["reason"]
    legs = events(rec, "transfer_booked")
    assert [e.payload["leg"] for e in legs] == ["airport_to_hotel", "hotel_to_airport"]
    assert all(e.payload["entitlement"] == "ground_transfer" and e.payload["reason"] for e in legs)
    assert {e.payload["transfer_ref"] for e in legs} <= set(pm.transfers)
    vouchers = events(rec, "voucher_issued")
    assert [e.payload["meal"] for e in vouchers] == ["dinner", "breakfast"]
    assert all(e.payload["entitlement"] == "meals" and e.payload["reason"] for e in vouchers)
    assert len(pm.vouchers) == 2
    assert rec.hotel.hotel_id == "HT8842" and len(rec.vouchers) == 2


async def test_all_steps_done_resolves_journey(setup):
    rt, rec = setup
    await run_duty_of_care(rt, rec, DONE)
    assert rec.state.status == S.RESOLVED
    assert not events(rec, "needs_human")


@pytest.mark.parametrize(
    "rebuild, missing",
    [
        (RebuildResult(new_flight="AC1240", seat_done=False, bag_done=True), "seat"),
        (RebuildResult(new_flight="AC1240", seat_done=True, bag_done=False), "bag"),
    ],
)
async def test_incomplete_rebuild_does_not_resolve(setup, rebuild, missing):
    rt, rec = setup
    await run_duty_of_care(rt, rec, rebuild)
    assert rec.state.status == S.RESOLVING
    (blocked,) = events(rec, "resolution_blocked")
    assert blocked.payload["missing"] == [missing]


async def test_no_new_flight_does_not_resolve(setup):
    rt, rec = setup
    await run_duty_of_care(rt, rec, RebuildResult(new_flight=None, seat_done=True, bag_done=True))
    assert rec.state.status == S.RESOLVING
    assert events(rec, "resolution_blocked")[0].payload["missing"] == ["flight"]
    assert not events(rec, "hotel_booked")


async def test_hotel_failure_needs_human_and_not_resolved(setup):
    rt, rec = setup
    rt.world.hotels_with_rooms.clear()
    await run_duty_of_care(rt, rec, DONE)
    assert rec.state.status == S.RESOLVING
    (nh,) = events(rec, "needs_human")
    assert nh.payload["step"] == "hotel" and nh.payload["error"]
    assert rec.hotel is None and not events(rec, "transfer_booked")
    assert rt.sim.partners.transfers == {}
    assert len(events(rec, "voucher_issued")) == 2  # meals are not held hostage to the hotel


async def test_force_majeure_still_gives_care_but_no_compensation(setup):
    rt, rec = setup
    rec.state._timeline[0].payload["reason"] = "severe weather"  # noqa: SLF001
    await run_duty_of_care(rt, rec, DONE)
    (a,) = events(rec, "duty_of_care_assessed")
    assert a.payload["hotel_required"] and a.payload["compensation_eligible"] is False
    assert rec.state.status == S.RESOLVED


async def test_rerun_after_resolved_is_a_noop(setup):
    rt, rec = setup
    await run_duty_of_care(rt, rec, DONE)
    n = len(rec.state.timeline)
    await run_duty_of_care(rt, rec, DONE)
    assert len(rec.state.timeline) == n and len(rt.sim.partners.hotel_bookings) == 1


async def test_api_reflects_bookings_and_resolved_status():
    app = create_app(FixedClock(DISRUPTION_DETECTED_AT))
    rt = app.state.runtime
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/scenario/trigger-cancellation",
            json={"flight": "AC1234", "reason": "unscheduled maintenance"},
        )
        (jid,) = r.json()["journey_ids"]
        await run_duty_of_care(rt, rt.journeys[jid], DONE)
        d = (await c.get(f"/journeys/{jid}")).json()
        n = len(rt.journeys[jid].state.timeline)
        sse = await c.get(f"/journeys/{jid}/events?limit={n}")
    assert d["status"] == "RESOLVED"
    assert d["hotel"]["hotel_id"] == "HT8842" and len(d["vouchers"]) == 2
    assert "hotel_booked" in sse.text and "voucher_issued" in sse.text
