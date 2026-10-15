"""#345: a replan gives the old seat back, so replanning there and back works (AC1240 -> AC1252 ->
AC1240) and ends RESOLVED with her seat, instead of stuck REPLANNING with none."""

from acme.domain.journey import DisruptionStatus as S
from acme.sim.dcs import dcs_state
from tests.governance.flow import Flow


def seat_on(f, flight):
    pnr = f.rt.world.pnrs["MSLV42"]
    return next((s.seat for s in pnr.seats if s.flight_number == flight), None)


async def test_replanning_to_another_flight_frees_the_seat_on_the_old_one():
    f = Flow()
    await f.cancel()
    await f.notify()
    assert seat_on(f, "AC1240") == "14A"
    assert "14A" not in f.rt.world.flights["AC1240"].open_seats["economy"]  # hers
    await f.say("Can I leave after 10am?")  # AC1240 -> AC1252
    assert seat_on(f, "AC1252") == "14A" and seat_on(f, "AC1240") is None
    assert "14A" in f.rt.world.flights["AC1240"].open_seats["economy"]  # given back
    assert ("AC1240", "14A") not in dcs_state(f.rt.sim).assigned
    assert f.rt.world.flights["AC1234"].open_seats.get("economy", []) == []  # cancelled: untouched
    await f.http.aclose()


async def test_replanning_there_and_back_ends_resolved_with_her_seat():
    f = Flow()
    await f.cancel()
    await f.notify()
    await f.say("Can I leave after 10am?")
    await f.say("Can I leave after 8am?")  # back to the morning flight
    state = f.rt.journeys[f.jid].state
    assert state.status is S.RESOLVED
    assert seat_on(f, "AC1240") == "14A"
    assert not [e for e in state.timeline if e.type == "needs_human"]
    await f.http.aclose()
