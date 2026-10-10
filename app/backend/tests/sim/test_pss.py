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


async def test_flight_get_and_manifest(connect):
    async with connect("pss") as s:
        f = await call(s, "flights.get", flight="AC0800")
        m = await call(s, "flight.manifest", flight="AC1234")
        empty = await call(s, "flight.manifest", flight="AC1240")
        assert (await s.call_tool("flights.get", {"flight": "ZZ1"})).isError
    assert f["origin"] == "SCL" and f["scheduled_departure"].startswith("2026-10-10T14:00")
    assert m["pnrs"] == [{"pnr": "MSLV42", "passenger_id": "P-MARIANA"}]
    assert empty["pnrs"] == []


async def test_manifest_unknown_flight_is_a_clear_error(connect):
    async with connect("pss") as s:
        res = await s.call_tool("flight.manifest", {"flight": "ZZ999"})
    assert res.isError
    assert "ZZ999" in str(res.content)
