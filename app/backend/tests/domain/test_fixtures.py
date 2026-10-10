from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from acme.domain.fixtures import GRU, SCL, mariana_world


def local(day: int, hhmm: str, tz):
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, 9 + day, h, m, tzinfo=tz)


def test_mariana_story():
    w = mariana_world()
    p = w.passengers["P-MARIANA"]
    pnr = w.pnrs["MSLV42"]
    assert p.tier == "diamond"
    assert [s.flight_number for s in pnr.segments] == ["AC1234", "AC0800"]
    assert (pnr.seats[0].flight_number, pnr.seats[0].seat) == ("AC1234", "14A")
    assert len(pnr.bags) == 1
    assert w.flights["AC0800"].scheduled_departure == local(1, "14:00", SCL)
    assert w.flights["AC1234"].status == "cancelled"


@pytest.mark.parametrize(
    ("number", "dep", "arr"),
    [
        ("AC1234", local(0, "23:30", GRU), local(1, "01:30", SCL)),
        ("AC1240", local(1, "08:15", GRU), local(1, "10:15", SCL)),
        ("AC1252", local(1, "11:40", GRU), local(1, "12:20", SCL)),
        ("AC1260", local(1, "13:10", GRU), local(1, "14:00", SCL)),
        ("AC0800", local(1, "14:00", SCL), None),
    ],
)
def test_pinned_times(number, dep, arr):
    f = mariana_world().flights[number]
    assert f.scheduled_departure == dep
    assert f.scheduled_arrival == arr


def test_gru_and_scl_are_both_utc_minus_3_on_fixture_date():
    w = mariana_world()
    assert w.flights["AC1240"].scheduled_departure.utcoffset() == timedelta(hours=-3)
    assert w.flights["AC1240"].scheduled_arrival.utcoffset() == timedelta(hours=-3)


def test_mct_fares_and_latest_feasible_arrival():
    w = mariana_world()
    inv = w.inventory[0]
    assert w.mct_minutes == 90
    assert (inv.flight_number, inv.cabin, inv.open_seats) == ("AC1252", "premium_business", 4)
    assert inv.standard_upgrade_fare == Decimal("290")
    assert inv.cost_floor == Decimal("55")
    latest = w.flights["AC0800"].scheduled_departure - timedelta(minutes=w.mct_minutes)
    assert latest == local(1, "12:30", SCL)
    assert w.flights["AC1252"].scheduled_arrival <= latest
    assert w.flights["AC1260"].scheduled_arrival > latest


def test_seats_and_hotel():
    w = mariana_world()
    assert "14A" in w.flights["AC1240"].open_seats["economy"]
    assert "14A" in w.flights["AC1252"].open_seats["economy"]
    assert "2A" in w.flights["AC1252"].open_seats["premium_business"]
    assert "HT8842" in w.hotels_with_rooms
