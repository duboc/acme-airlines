from tests.sim.conftest import call

MARIANA = "MSLV42"


async def err(session, tool, **args):
    res = await session.call_tool(tool, args)
    assert res.isError, res.content
    return res


async def test_seatmap_lists_open_seats(connect):
    async with connect("dcs") as s:
        m = await call(s, "seatmap.get", flight="AC1240")
        none = await err(s, "seatmap.get", flight="ZZ0")
    assert m["flight"] == "AC1240" and m["seats"] == {"economy": ["14A"]} and m["held"] == {}
    assert none.isError


async def test_hold_reserves_seat_for_pnr_and_second_pnr_fails(connect, sim):
    async with connect("dcs") as s:
        held = await call(s, "seat.hold", flight="AC1240", seat="14A", pnr=MARIANA)
        again = await call(s, "seat.hold", flight="AC1240", seat="14A", pnr=MARIANA)
        await err(s, "seat.hold", flight="AC1240", seat="14A", pnr="OTHER99")
        m = await call(s, "seatmap.get", flight="AC1240")
    assert held["status"] == "held" and again["status"] == "held"
    assert m["held"] == {"14A": MARIANA} and m["seats"] == {"economy": []}


async def test_other_pnr_cannot_hold_even_if_pnr_exists(connect, sim):
    from acme.domain.models import PNR, Segment

    sim.world.pnrs["OTHER1"] = PNR(
        locator="OTHER1", passenger_id="P-MARIANA", segments=[Segment(flight_number="AC1240")]
    )
    async with connect("dcs") as s:
        await call(s, "seat.hold", flight="AC1240", seat="14A", pnr=MARIANA)
        await err(s, "seat.hold", flight="AC1240", seat="14A", pnr="OTHER1")


async def test_hold_rejects_unavailable_seat(connect):
    async with connect("dcs") as s:
        await err(s, "seat.hold", flight="AC1240", seat="99Z", pnr=MARIANA)


async def test_assign_then_boarding_pass_shows_flight_seat_cabin(connect, sim):
    await sim.rebook(MARIANA, "AC1240")  # the scenario rebooks before seating
    async with connect("dcs") as s:
        await call(s, "seat.hold", flight="AC1240", seat="14A", pnr=MARIANA)
        a = await call(s, "seat.assign", flight="AC1240", seat="14A", pnr=MARIANA, cabin="economy")
        bp = await call(s, "boarding_pass.issue", pnr=MARIANA, flight="AC1240")
        m = await call(s, "seatmap.get", flight="AC1240")
    assert a["seat"] == "14A" and a["cabin"] == "economy"
    assert (bp["flight"], bp["seat"], bp["cabin"]) == ("AC1240", "14A", "economy")
    assert bp["boarding_pass_id"] and bp["passenger_name"] == "Mariana Silva"
    assert m["seats"] == {"economy": []} and m["held"] == {}
    assert any(x.flight_number == "AC1240" for x in sim.world.pnrs[MARIANA].seats)


async def test_assign_premium_upgrade_seat(connect):
    async with connect("dcs") as s:
        a = await call(s, "seat.assign", flight="AC1252", seat="2A", pnr=MARIANA,
                       cabin="premium_business")  # fmt: skip
    assert a["cabin"] == "premium_business"


async def test_assign_errors(connect):
    async with connect("dcs") as s:
        await call(s, "seat.hold", flight="AC1240", seat="14A", pnr=MARIANA)
        await err(s, "seat.assign", flight="AC1240", seat="14A", pnr="OTHER99", cabin="economy")
        await err(
            s, "seat.assign", flight="AC1240", seat="14A", pnr=MARIANA, cabin="premium_business"
        )
        await err(s, "seat.assign", flight="AC1240", seat="14A", pnr=MARIANA, cabin="first")


async def test_boarding_pass_requires_assignment_and_segment(connect, sim):
    async with connect("dcs") as s:
        await err(s, "boarding_pass.issue", pnr=MARIANA, flight="AC1240")  # not on PNR yet
        await sim.rebook(MARIANA, "AC1240")
        await err(s, "boarding_pass.issue", pnr=MARIANA, flight="AC1240")  # no seat yet
        await err(s, "boarding_pass.issue", pnr=MARIANA, flight="AC1260")
        await err(s, "boarding_pass.issue", pnr="NOPE", flight="AC1240")


async def test_bag_get_and_reroute(connect):
    async with connect("worldtracer") as s:
        before = await call(s, "bag.get", tag="BAG0001")
        moved = await call(s, "bag.reroute", tag="BAG0001", flight="AC1240")
        after = await call(s, "bag.get", tag="BAG0001")
    assert before["routing"] == "AC1234" and before["pnr"] == MARIANA
    assert moved["routing"] == "AC1240" and after["routing"] == "AC1240"


async def test_bag_errors(connect):
    async with connect("worldtracer") as s:
        await err(s, "bag.get", tag="NOPE")
        await err(s, "bag.reroute", tag="NOPE", flight="AC1240")
        await err(s, "bag.reroute", tag="BAG0001", flight="ZZ0")
        await err(s, "bag.reroute", tag="BAG0001", flight="AC1234")  # cancelled flight


async def test_registry_contains_dcs_and_worldtracer():
    from acme.sim.app import DEFAULT_REGISTRY

    assert {"dcs", "worldtracer"} <= set(DEFAULT_REGISTRY)
