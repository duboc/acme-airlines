"""Boundary and message tests for the policy code, written to kill surviving mutants (#232).

Each test pins one threshold from docs/product/policy-rules.md at, just below and just above the
edge, or one reason string that downstream code (and the passenger-facing explanation) relies on.
"""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from acme.domain.models import Flight
from acme.policy import duty_of_care as doc
from acme.policy.connection_check import check_flights
from acme.policy.guardrail_offer import evaluate
from tests.policy.test_duty_of_care import ev
from tests.policy.test_guardrail_offer import INVENTORY, make_journey, make_offer


# --- duty of care: meal windows (lunch 11:00-15:00, dinner 18:00-23:00, half-open) -------------
@pytest.mark.parametrize(
    ("start", "meal"),
    [
        (time(10, 59), None), (time(11, 0), "lunch"), (time(14, 59), "lunch"),
        (time(15, 0), None), (time(17, 59), None), (time(18, 0), "dinner"),
        (time(22, 59), "dinner"), (time(23, 0), None),
    ],
)  # fmt: skip
def test_meal_window_edges_are_inclusive_at_the_start_and_exclusive_at_the_end(start, meal):
    meals = ev(wait_hours=3.0, wait_start=start, requires_overnight=False).meal_vouchers
    assert meals == ([meal] if meal else [])


@pytest.mark.parametrize(
    ("hours", "overnight", "breakfast"),
    [(3.99, True, False), (4.0, True, True), (4.01, True, True), (10.0, False, False)],
)
def test_breakfast_needs_an_overnight_wait_of_at_least_four_hours(hours, overnight, breakfast):
    """DOC-04: breakfast needs an overnight wait of >= 4h."""
    meals = ev(wait_hours=hours, requires_overnight=overnight, wait_start=time(3)).meal_vouchers
    assert ("breakfast" in meals) is breakfast


def test_breakfast_is_not_given_for_a_long_wait_without_an_overnight_stay():
    assert ev(wait_hours=6.0, requires_overnight=False, wait_start=time(3)).meal_vouchers == []


def test_no_meals_below_two_hours_even_inside_a_meal_window():
    """DOC-02 / DOC-03: no meal voucher below 2h."""
    assert ev(wait_hours=1.99, wait_start=time(12), requires_overnight=False).meal_vouchers == []


def test_a_meal_window_at_the_two_hour_edge_gets_the_meal():
    """DOC-02: lunch at exactly 2h inside 11:00-15:00."""
    assert ev(wait_hours=2.0, wait_start=time(12), requires_overnight=False).meal_vouchers == [
        "lunch"
    ]


# --- duty of care: validation messages and thresholds --------------------------------------------
def test_a_non_brazilian_origin_is_refused_with_a_clear_message():
    with pytest.raises(
        ValueError, match=r"^only departures from Brazil \(ANAC 400\) are supported$"
    ):
        ev(origin_airport="SCL")


def test_zero_wait_is_valid_and_a_negative_wait_is_refused_with_a_clear_message():
    assert ev(wait_hours=0.0, requires_overnight=False).meal_vouchers == []
    with pytest.raises(ValueError, match=r"^wait_hours must be >= 0$"):
        ev(wait_hours=-0.01)


@pytest.mark.parametrize(
    ("hours", "included", "suffix"),
    [(0.99, False, " (threshold not met)"), (1.0, True, ""), (1.01, True, "")],
)
def test_communication_starts_at_exactly_one_hour(hours, included, suffix):
    """DOC-01: communication from exactly 1h."""
    e = ev(wait_hours=hours, requires_overnight=False)
    assert e.communication_included is included
    assert e.rule_reasons["communication"] == f"{doc.ART27}, I{suffix}"


@pytest.mark.parametrize(
    ("hours", "reason"),
    [
        (1.99, "ANAC 400 Art. 27, II (threshold not met)"),
        (2.0, "ANAC 400 Art. 27, II (no meal window)"),
        (3.9, "ANAC 400 Art. 27, II (no meal window)"),
    ],
)
def test_meal_reason_distinguishes_below_threshold_from_no_meal_window(hours, reason):
    e = ev(wait_hours=hours, wait_start=time(4), requires_overnight=False)
    assert e.meal_vouchers == [] and e.rule_reasons["meals"] == reason


@pytest.mark.parametrize(
    ("hours", "hotel_reason"),
    [(3.99, "ANAC 400 Art. 27, III (threshold not met)"),
     (4.0, "ANAC 400 Art. 27, III (no overnight stay required)")],
)  # fmt: skip
def test_hotel_threshold_is_four_hours_and_a_long_day_wait_needs_no_hotel_or_transfer(
    hours, hotel_reason
):
    e = ev(wait_hours=hours, requires_overnight=False)
    assert not e.hotel_required and not e.ground_transfer_required
    assert e.rule_reasons["hotel"] == e.rule_reasons["ground_transfer"] == hotel_reason


def test_force_majeure_cites_the_liability_exclusion_and_still_gives_care():
    e = ev(disruption_reason="force_majeure")
    assert (
        e.rule_reasons["compensation"] == "CDC Art. 14, § 3º, II (excludente de responsabilidade)"
    )
    assert not e.compensation_eligible and e.hotel_required and e.meal_vouchers


def test_eu_reasons_for_an_eu_touching_route_and_a_third_country_route():
    assert ev(destination_airport="MAD").rule_reasons["eu261"] == (
        "EU261 may apply: route touches an EU airport (Art. 3(1)); refer for review"
    )
    assert ev().rule_reasons["eu261"] == "EU261 non-applicable: third-country route (Art. 3(1))"


def test_a_short_delay_is_not_reaccommodated_and_says_so():
    """DOC-09: a short delay is not re-accommodated."""
    e = ev(disruption_type="delay", wait_hours=1.5, requires_overnight=False)
    assert not e.reaccommodation_required
    assert e.rule_reasons["reaccommodation"] == "ANAC 400 Art. 21 (threshold not met)"


def test_a_long_delay_is_reaccommodated_like_a_cancellation():
    """DOC-09: a delay of >= 4h is re-accommodated."""
    e = ev(disruption_type="delay", wait_hours=4.0, requires_overnight=False)
    assert e.reaccommodation_required
    assert e.rule_reasons["reaccommodation"] == "ANAC 400 Art. 21, I & Art. 28"


# --- connection check ----------------------------------------------------------------------------
def _flight(number, origin, destination, dep, arr=None):
    return Flight(number=number, origin=origin, destination=destination,
                  scheduled_departure=dep, scheduled_arrival=arr)  # fmt: skip


T0 = datetime(2026, 10, 10, 8, 0, tzinfo=UTC)


def test_flights_that_do_not_meet_at_one_airport_are_refused_with_a_clear_message():
    a = _flight("AC1", "GRU", "SCL", T0, T0 + timedelta(hours=2))
    b = _flight("AC2", "EZE", "MAD", T0 + timedelta(hours=5))
    with pytest.raises(ValueError, match=r"^flights must connect at the same airport$"):
        check_flights(a, b)


def test_check_flights_uses_the_connection_airport_and_the_given_mct():
    a = _flight("AC1", "GRU", "SCL", T0, T0 + timedelta(hours=2))
    b = _flight("AC2", "SCL", "MAD", T0 + timedelta(hours=2, minutes=60))
    assert check_flights(a, b, mct_minutes=60).feasible  # explicit MCT honoured (exact edge)
    assert not check_flights(a, b, mct_minutes=61).feasible
    # no explicit MCT: SCL's configured 90 minutes applies (the 60 minute connection fails)
    default = check_flights(a, b)
    assert (default.mct_minutes, default.feasible) == (90, False)
    other = _flight("AC3", "GRU", "EZE", T0, T0 + timedelta(hours=2))
    onward = _flight("AC4", "EZE", "MAD", T0 + timedelta(hours=2, minutes=60))
    assert check_flights(other, onward).mct_minutes == 90  # airport without its own MCT: fallback


# --- guardrail edges -----------------------------------------------------------------------------
def test_exactly_one_open_seat_is_enough():
    one = INVENTORY.model_copy(update={"open_seats": 1})
    d = evaluate(make_journey(), make_offer(), one)
    assert d.approved


def test_a_price_equal_to_the_standard_fare_is_not_a_discount_even_for_an_ineligible_tier():
    # With an eligible tier the tier cap already rejects it; with none, only the strict
    # "price must be below standard" rule can.
    d = evaluate(
        make_journey(tier="silver"),
        make_offer(price=Decimal("290"), net_margin=Decimal("235")),
        INVENTORY,
    )
    assert "not_a_genuine_discount" in d.reasons and "tier_not_eligible" in d.reasons


def test_check_flights_looks_up_the_mct_of_the_connection_airport(monkeypatch):
    from acme.policy import connection_check

    monkeypatch.setitem(connection_check.DEFAULT_MCT_MINUTES, "SCL", 45)  # differs from fallback
    a = _flight("AC1", "GRU", "SCL", T0, T0 + timedelta(hours=2))
    b = _flight("AC2", "SCL", "MAD", T0 + timedelta(hours=2, minutes=50))
    result = check_flights(a, b)
    assert (result.mct_minutes, result.margin_minutes, result.feasible) == (45, 5, True)


def test_a_naive_arrival_is_refused_naming_the_arrival_argument():
    from acme.policy.connection_check import check_connection

    with pytest.raises(ValueError, match=r"^arrival must be timezone-aware$"):
        check_connection("SCL", datetime(2026, 1, 1, 8), T0)


def test_a_naive_onward_departure_is_refused_naming_that_argument():
    from acme.policy.connection_check import check_connection, latest_arrival

    with pytest.raises(ValueError, match=r"^onward_departure must be timezone-aware$"):
        check_connection("SCL", T0, datetime(2026, 1, 1, 10))
    with pytest.raises(ValueError, match=r"^onward_departure must be timezone-aware$"):
        latest_arrival("SCL", datetime(2026, 1, 1, 10))


def test_connection_minutes_are_whole_minutes_rounded_down():
    from acme.policy.connection_check import check_connection

    r = check_connection("SCL", T0, T0 + timedelta(minutes=100, seconds=59), mct_minutes=90)
    assert (r.connection_minutes, r.margin_minutes) == (100, 10)


def test_latest_arrival_uses_the_airports_own_mct_then_the_fallback(monkeypatch):
    from acme.policy import connection_check as cc

    monkeypatch.setitem(cc.DEFAULT_MCT_MINUTES, "SCL", 45)
    at, mct = cc.latest_arrival("SCL", T0)
    assert (mct, at) == (45, T0 - timedelta(minutes=45))
    at, mct = cc.latest_arrival("XXX", T0)  # no entry: the fallback applies
    assert (mct, at) == (cc.FALLBACK_MCT_MINUTES, T0 - timedelta(minutes=cc.FALLBACK_MCT_MINUTES))
    assert cc.latest_arrival("SCL", T0, mct_minutes=10) == (T0 - timedelta(minutes=10), 10)


def test_a_connection_that_is_already_missed_rounds_down_to_a_negative_minute():
    from acme.policy.connection_check import check_connection

    r = check_connection("SCL", T0, T0 - timedelta(seconds=30), mct_minutes=0)
    assert (r.connection_minutes, r.feasible) == (-1, False)  # floor, not truncation toward zero


@pytest.mark.parametrize(
    ("reason", "hours", "eligible"),
    [
        ("technical_maintenance", 3.99, False),
        ("technical_maintenance", 4.0, True),
        ("force_majeure", 4.0, False),
    ],
)
def test_doc10_compensation_boundary(reason, hours, eligible):
    e = ev(disruption_reason=reason, wait_hours=hours)
    assert e.compensation_eligible is eligible


@pytest.mark.parametrize(
    ("hours", "start", "meals"),
    [(1.99, time(19), []), (2.0, time(19), ["dinner"]), (2.0, time(17, 59), [])],
)
def test_doc03_dinner_needs_two_hours_inside_the_evening_window(hours, start, meals):
    """DOC-03: dinner needs wait >= 2h and a start in 18:00-23:00."""
    assert ev(wait_hours=hours, wait_start=start, requires_overnight=False).meal_vouchers == meals


@pytest.mark.parametrize(
    ("kind", "hours", "reaccommodated"),
    [
        ("cancellation", 0.0, True),
        ("delay", 3.99, False),
        ("delay", 4.0, True),
    ],
)
def test_doc09_reaccommodation_boundary(kind, hours, reaccommodated):
    """DOC-09: cancellation OR wait >= 4h gets free re-accommodation."""
    e = ev(disruption_type=kind, wait_hours=hours, requires_overnight=False)
    assert e.reaccommodation_required is reaccommodated
