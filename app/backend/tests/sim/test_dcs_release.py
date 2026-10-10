from tests.sim.conftest import call

PNR = "MSLV42"


async def err(session, tool, **args):
    res = await session.call_tool(tool, args)
    assert res.isError, res.content


async def test_release_returns_an_assigned_seat_to_inventory(connect, sim):
    async with connect("dcs") as s:
        await call(s, "seat.assign", flight="AC1240", seat="14A", pnr=PNR, cabin="economy")
        assert (await call(s, "seatmap.get", flight="AC1240"))["seats"]["economy"] == []
        out = await call(s, "seat.release", flight="AC1240", seat="14A", pnr=PNR)
        assert out["status"] == "released"
        assert (await call(s, "seatmap.get", flight="AC1240"))["seats"]["economy"] == ["14A"]
    assert [x for x in sim.world.pnrs[PNR].seats if x.flight_number == "AC1240"] == []


async def test_release_of_a_replaced_seat_after_upgrade(connect, sim):
    sim.world.flights["AC1252"].open_seats["economy"] = ["14A"]
    async with connect("dcs") as s:
        await call(s, "seat.assign", flight="AC1252", seat="14A", pnr=PNR, cabin="economy")
        await call(s, "seat.assign", flight="AC1252", seat="2A", pnr=PNR, cabin="premium_business")
        # 14A is no longer on the PNR but is still blocked until released
        assert "14A" not in (await call(s, "seatmap.get", flight="AC1252"))["seats"]["economy"]
        await call(s, "seat.release", flight="AC1252", seat="14A", pnr=PNR)
        seats = (await call(s, "seatmap.get", flight="AC1252"))["seats"]
    assert "14A" in seats["economy"]
    assert [x.seat for x in sim.world.pnrs[PNR].seats if x.flight_number == "AC1252"] == ["2A"]


async def test_release_is_idempotent_and_clears_holds(connect):
    async with connect("dcs") as s:
        await call(s, "seat.hold", flight="AC1240", seat="14A", pnr=PNR)
        a = await call(s, "seat.release", flight="AC1240", seat="14A", pnr=PNR)
        b = await call(s, "seat.release", flight="AC1240", seat="14A", pnr=PNR)
        m = await call(s, "seatmap.get", flight="AC1240")
    assert a["status"] == b["status"] == "released"
    assert m["held"] == {} and m["seats"]["economy"] == ["14A"]


async def test_only_the_holder_can_release(connect, sim):
    from acme.domain.models import PNR as Pnr
    from acme.domain.models import Segment

    sim.world.pnrs["OTHER1"] = Pnr(
        locator="OTHER1", passenger_id="P-MARIANA", segments=[Segment(flight_number="AC1240")]
    )
    async with connect("dcs") as s:
        await call(s, "seat.hold", flight="AC1240", seat="14A", pnr=PNR)
        await err(s, "seat.release", flight="AC1240", seat="14A", pnr="OTHER1")
        await call(s, "seat.assign", flight="AC1240", seat="14A", pnr=PNR, cabin="economy")
        await err(s, "seat.release", flight="AC1240", seat="14A", pnr="OTHER1")
        assert (await call(s, "seatmap.get", flight="AC1240"))["seats"]["economy"] == []


async def test_release_errors(connect):
    async with connect("dcs") as s:
        await err(s, "seat.release", flight="AC1240", seat="99Z", pnr=PNR)
        await err(s, "seat.release", flight="ZZ0", seat="14A", pnr=PNR)
        await err(s, "seat.release", flight="AC1240", seat="14A", pnr="NOPE")
