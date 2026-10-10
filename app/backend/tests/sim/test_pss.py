from tests.sim.conftest import call


async def test_search_returns_pinned_times(connect):
    async with connect("pss") as s:
        res = await call(s, "flights.search", origin="GRU", dest="SCL",
                         after="2026-10-10T00:00:00-03:00")  # fmt: skip
    flights = {f["number"]: f for f in res["flights"]}
    assert set(flights) == {"AC1240", "AC1252", "AC1260"}
    assert flights["AC1252"]["scheduled_departure"].startswith("2026-10-10T11:40:00")
    assert flights["AC1252"]["scheduled_arrival"].startswith("2026-10-10T12:20:00")


async def test_search_before_filters(connect):
    async with connect("pss") as s:
        res = await call(s, "flights.search", origin="GRU", dest="SCL",
                         after="2026-10-10T00:00:00-03:00",
                         before="2026-10-10T09:00:00-03:00")  # fmt: skip
    assert [f["number"] for f in res["flights"]] == ["AC1240"]


async def test_rebook_then_get_shows_new_flight(connect):
    async with connect("pss") as s:
        before = await call(s, "pnr.get", pnr="MSLV42")
        assert [x["flight_number"] for x in before["segments"]] == ["AC1234", "AC0800"]
        await call(s, "pnr.rebook", pnr="MSLV42", flight="AC1240")
        after = await call(s, "pnr.get", pnr="MSLV42")
    assert [x["flight_number"] for x in after["segments"]] == ["AC1240", "AC0800"]
    assert after["bags"] and not after["seats"]  # bag kept, old seat released


async def test_rebook_errors(connect):
    async with connect("pss") as s:
        assert (await s.call_tool("pnr.rebook", {"pnr": "NOPE", "flight": "AC1240"})).isError
        assert (await s.call_tool("pnr.rebook", {"pnr": "MSLV42", "flight": "ZZ1"})).isError
