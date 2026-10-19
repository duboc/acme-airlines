from datetime import timedelta

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
    legs = events(rec, "transfer_arranged")
    assert [e.payload["leg"] for e in legs] == ["airport_to_hotel", "hotel_to_airport"]
    assert all(e.payload["entitlement"] == "ground_transfer" and e.payload["reason"] for e in legs)
    assert {e.payload["transfer_ref"] for e in legs} <= set(pm.transfers)
    vouchers = events(rec, "voucher_issued")
    assert [e.payload["meal_type"] for e in vouchers] == ["dinner", "breakfast"]
    assert all(e.payload["kind"] == "meal" for e in vouchers)
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
    assert rec.hotel is None and not events(rec, "transfer_arranged")
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


# --- a same-day secondary cancellation reschedules the booked hotel pickup (#490) ----------------
PICKUP_0 = "2026-10-10T05:15:00-03:00"  # AC1240 08:15 minus 3 h
PICKUP_1 = "2026-10-10T08:40:00-03:00"  # AC1252 11:40 minus 3 h
TO_HOTEL = "2026-10-09T23:03:00-03:00"  # one hour after the cancellation


async def with_hotel_then_secondary():
    from acme.llm import FakeLlm

    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    rec = rt.journeys[jid]
    assert rec.hotel is not None and rec.state.status == S.RESOLVED
    active = lambda: sorted(  # noqa: E731
        (t["origin"], t["destination"], t["pickup_at"]) for t in rt.sim.partners.transfers.values()
    )
    assert active() == [("GRU", "HT8842", TO_HOTEL), ("HT8842", "GRU", PICKUP_0)]
    return rt, rec, active


async def test_a_same_day_secondary_rebook_moves_the_pickup_and_keeps_the_hotel_leg():
    rt, rec, active = await with_hotel_then_secondary()
    rt.clock.advance(timedelta(hours=8, minutes=57))  # 07:00 on the 10th: the morning of AC1240
    await rt.trigger_cancellation("AC1240", "weather")  # AC1252 11:40: a same-day wait, DOC-08
    assessed = events(rec, "duty_of_care_assessed")[-1].payload
    assert not assessed["hotel_required"] and not assessed["ground_transfer_required"]
    assert active() == [("GRU", "HT8842", TO_HOTEL), ("HT8842", "GRU", PICKUP_1)]  # moved, once
    legs = [(e.payload["leg"], e.payload["reason"]) for e in events(rec, "transfer_arranged")]
    assert [leg for leg, _ in legs] == ["airport_to_hotel", "hotel_to_airport", "hotel_to_airport"]
    assert legs[-1][1] == "rescheduled for the new departure"
    assert len(rt.sim.partners.hotel_bookings) == 1  # the hotel is not booked again
    assert rec.state.status == S.RESOLVED and not events(rec, "needs_human")


async def test_an_overnight_secondary_rebook_also_ends_with_one_moved_pickup():
    rt, rec, active = await with_hotel_then_secondary()
    await rt.trigger_cancellation("AC1240", "weather")  # still the night of the 9th: DOC-05 again
    assert events(rec, "duty_of_care_assessed")[-1].payload["ground_transfer_required"]
    assert active() == [("GRU", "HT8842", TO_HOTEL), ("HT8842", "GRU", PICKUP_1)]


async def test_a_failed_pickup_booking_keeps_the_old_pickup_and_asks_for_a_person():
    from acme.sim.client import ToolError

    rt, rec, active = await with_hotel_then_secondary()
    rt.clock.advance(timedelta(hours=8, minutes=57))
    real = rt.client.call

    async def call(server, tool, **kw):
        if tool == "transfer.book":
            raise ToolError("concierge/transfer.book: down")
        return await real(server, tool, **kw)

    rt.client.call = call
    await rt.trigger_cancellation("AC1240", "weather")
    assert active() == [("GRU", "HT8842", TO_HOTEL), ("HT8842", "GRU", PICKUP_0)]  # not cancelled
    (nh,) = [e for e in events(rec, "needs_human") if e.payload["step"].startswith("transfer")]
    assert nh.payload["step"] == "transfer:hotel_to_airport"
    assert rec.state.status != S.RESOLVED


async def test_a_same_day_wait_without_a_hotel_books_no_transfer(setup):
    rt, rec = setup
    flights = rt.world.flights
    original = flights["AC1234"].scheduled_departure.replace(hour=10, minute=0)
    flights["AC1234"].scheduled_departure = original
    flights["AC1240"].scheduled_departure = original + timedelta(hours=2)  # DOC-08
    assert rec.hotel is None
    await run_duty_of_care(rt, rec, DONE)
    assert rt.sim.partners.transfers == {} and not events(rec, "transfer_arranged")
    assert rec.state.status == S.RESOLVED


# --- a manual AC1234 trigger on the live clock keeps the seeded wait (#494) ---------------------
@pytest.mark.parametrize(
    "reason, compensable", [("unscheduled maintenance", True), ("severe weather", False)]
)
async def test_a_live_clock_trigger_still_gets_dinner_and_breakfast_and_ordered_pickups(
    reason, compensable
):
    from acme.domain.clock import SystemClock
    from acme.llm import FakeLlm

    rt = CustomerRuntime(llm=FakeLlm())  # the live clock: days after the seeded 2026-10-09
    assert isinstance(rt.clock, SystemClock)
    (jid,) = await rt.trigger_cancellation("AC1234", reason)
    rec = rt.journeys[jid]
    assessed = events(rec, "duty_of_care_assessed")[-1].payload
    assert assessed["meal_vouchers"] == ["dinner", "breakfast"]
    assert assessed["compensation_eligible"] is compensable
    assert sorted(str(v.amount) for v in rec.vouchers) == ["12.00", "25.00"]
    assert [(e.payload["leg"]) for e in events(rec, "transfer_arranged")] == [
        "airport_to_hotel",
        "hotel_to_airport",
    ]
    assert sorted(t["pickup_at"] for t in rt.sim.partners.transfers.values()) == [
        "2026-10-09T23:03:00-03:00",
        "2026-10-10T05:15:00-03:00",
    ]
    assert isinstance(rt.clock, SystemClock)  # the clock itself is untouched (#321)
    assert rec.state.timeline[0].timestamp != DISRUPTION_DETECTED_AT


async def test_a_live_clock_resident_gets_the_home_transfer_an_hour_into_the_seeded_wait():
    from acme.llm import FakeLlm

    rt = CustomerRuntime(llm=FakeLlm())
    rt.world.passengers["P-MARIANA"].origin_resident = True  # DOC-06
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    (home,) = rt.sim.partners.transfers.values()
    assert (home["origin"], home["destination"]) == ("GRU", "HOME")
    assert home["pickup_at"] == "2026-10-09T23:03:00-03:00"
    assert rt.journeys[jid].hotel is None
