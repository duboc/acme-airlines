import pytest

from acme.customer.runtime import CustomerRuntime
from acme.customer.specialists.seat import nearest_window_seat
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus as S
from acme.sim.dcs import dcs_state


@pytest.fixture
def rt():
    return CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT))


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
    ],
)
def test_nearest_window_seat(open_seats, want, expected):
    assert nearest_window_seat(open_seats, want) == expected
