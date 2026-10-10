from datetime import datetime, timedelta, timezone

import pytest

from acme.domain.fixtures import mariana_world
from acme.policy.connection_check import DEFAULT_MCT_MINUTES, check_connection, check_flights

TZ = timezone(timedelta(hours=-3))


def at(h, m=0):
    return datetime(2026, 10, 10, h, m, tzinfo=TZ)


def test_feasible_with_positive_margin():
    r = check_connection("SCL", arrival=at(10, 15), onward_departure=at(14), mct_minutes=90)
    assert r.feasible and r.connection_minutes == 225 and r.margin_minutes == 135


def test_exactly_mct_is_feasible_with_zero_margin():
    r = check_connection("SCL", at(12, 30), at(14), mct_minutes=90)
    assert r.feasible and r.margin_minutes == 0


@pytest.mark.parametrize(("arr", "margin"), [(at(13), -30), (at(14), -90), (at(15), -150)])
def test_infeasible(arr, margin):
    r = check_connection("SCL", arr, at(14), mct_minutes=90)
    assert not r.feasible and r.margin_minutes == margin


def test_default_mct_is_per_airport_and_overridable():
    assert DEFAULT_MCT_MINUTES["SCL"] == 90
    assert check_connection("SCL", at(12, 30), at(14)).feasible
    assert check_connection("XXX", at(12, 31), at(14)).mct_minutes == 90
    assert not check_connection("SCL", at(12, 30), at(14), mct_minutes=120).feasible


def test_naive_datetimes_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        check_connection("SCL", datetime(2026, 10, 10, 10), at(14))
    with pytest.raises(ValueError, match="timezone-aware"):
        check_connection("SCL", at(10), datetime(2026, 10, 10, 14))


def test_fixture_flights():
    w = mariana_world()
    onward = w.flights["AC0800"]
    results = {n: check_flights(w.flights[n], onward, w.mct_minutes).feasible
               for n in ("AC1240", "AC1252", "AC1260")}  # fmt: skip
    assert results == {"AC1240": True, "AC1252": True, "AC1260": False}


def test_flights_must_meet_at_one_airport_and_have_arrival():
    w = mariana_world()
    with pytest.raises(ValueError, match="same airport"):
        check_flights(w.flights["AC1240"], w.flights["AC1252"])
    with pytest.raises(ValueError, match="no scheduled arrival"):
        no_arrival = w.flights["AC1240"].model_copy(update={"scheduled_arrival": None})
        check_flights(no_arrival, w.flights["AC0800"])
