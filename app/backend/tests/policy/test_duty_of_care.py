from datetime import time

import pytest

from acme.policy.duty_of_care import DutyOfCareEntitlements, evaluate_duty_of_care

EVENING = time(22, 3)


def ev(**over):
    args = dict(
        origin_airport="GRU", destination_airport="SCL", disruption_type="cancellation",
        disruption_reason="technical_maintenance", wait_hours=10.2, wait_start=EVENING,
        requires_overnight=True, passenger_is_origin_resident=False,
        passenger_has_special_needs=False,
    )  # fmt: skip
    return evaluate_duty_of_care(**{**args, **over})


def test_mariana_overnight_cancellation():
    e = ev()
    assert isinstance(e, DutyOfCareEntitlements)
    assert e.communication_included
    assert e.meal_vouchers == ["dinner", "breakfast"]
    assert e.hotel_required and e.ground_transfer_required
    assert e.reaccommodation_required and e.compensation_eligible
    assert e.rule_reasons["hotel"] == "ANAC 400 Art. 27, III"
    assert e.rule_reasons["ground_transfer"] == "ANAC 400 Art. 27, III"
    assert e.rule_reasons["meals"] == "ANAC 400 Art. 27, II"
    assert e.rule_reasons["reaccommodation"] == "ANAC 400 Art. 21, I & Art. 28"
    assert e.rule_reasons["compensation"] == "CDC Art. 14 / STJ REsp 1.584.465 (fortuito interno)"
    assert "EU261 non-applicable" in e.rule_reasons["eu261"]


def test_every_item_always_carries_a_reason():
    keys = {"communication", "meals", "hotel", "ground_transfer", "reaccommodation",
            "compensation", "eu261"}  # fmt: skip
    for kw in ({}, {"wait_hours": 0.5, "requires_overnight": False}):
        assert set(ev(**kw).rule_reasons) == keys
        assert all(ev(**kw).rule_reasons.values())


def test_same_day_within_two_hours_needs_no_hotel_or_meal():
    e = ev(wait_hours=1.5, requires_overnight=False, disruption_type="delay")
    assert e.communication_included and not e.hotel_required and not e.ground_transfer_required
    assert e.meal_vouchers == [] and not e.reaccommodation_required
    assert "threshold not met" in e.rule_reasons["hotel"]


def test_no_communication_under_one_hour():
    """DOC-01: no communication below the one-hour threshold."""
    e = ev(wait_hours=0.9, requires_overnight=False, disruption_type="delay")
    assert not e.communication_included
    assert "threshold not met" in e.rule_reasons["communication"]


@pytest.mark.parametrize(
    ("start", "wait", "overnight", "meals"),
    [
        (time(12), 2.0, False, ["lunch"]),
        (time(19), 2.5, False, ["dinner"]),
        (time(16), 2.5, False, []),
        (time(23, 30), 3.0, False, []),
        (time(19), 12, True, ["dinner", "breakfast"]),
        (time(1), 8, True, ["breakfast"]),
        (time(12), 1.9, False, []),
    ],
)
def test_meal_windows(start, wait, overnight, meals):
    """DOC-02 / DOC-03 / DOC-04: lunch, dinner and overnight breakfast vouchers by wait start."""
    assert ev(wait_start=start, wait_hours=wait, requires_overnight=overnight,
              disruption_type="delay").meal_vouchers == meals  # fmt: skip


def test_resident_gets_transfer_but_no_hotel():
    e = ev(passenger_is_origin_resident=True)
    assert not e.hotel_required and e.ground_transfer_required
    assert e.rule_reasons["hotel"] == "ANAC 400 Art. 27, § 1º"
    assert e.rule_reasons["ground_transfer"] == "ANAC 400 Art. 27, § 1º"


def test_doc06_resident_and_special_needs_overnight_gets_transfer_but_no_hotel():
    e = ev(passenger_is_origin_resident=True, passenger_has_special_needs=True, wait_hours=10.2)
    assert e.hotel_required is False and e.ground_transfer_required is True
    assert e.rule_reasons["hotel"] == "ANAC 400 Art. 27, § 1º"
    assert e.rule_reasons["ground_transfer"] == "ANAC 400 Art. 27, § 1º"


def test_special_needs_daytime_four_hours_gets_hotel():
    e = ev(requires_overnight=False, wait_hours=5, passenger_has_special_needs=True,
           wait_start=time(10), disruption_type="delay")  # fmt: skip
    assert e.hotel_required and e.ground_transfer_required
    assert e.rule_reasons["hotel"] == "ANAC 400 Art. 27, § 2º"


def test_four_hours_no_overnight_no_special_needs_gets_no_hotel():
    e = ev(requires_overnight=False, wait_hours=5, disruption_type="delay")
    assert not e.hotel_required and e.reaccommodation_required
    assert "no overnight" in e.rule_reasons["hotel"]


def test_force_majeure_removes_compensation_but_not_care():
    e = ev(disruption_reason="force_majeure")
    assert not e.compensation_eligible and e.hotel_required and e.meal_vouchers
    assert "Art. 14, § 3º, II" in e.rule_reasons["compensation"]


def test_short_technical_delay_is_not_compensable():
    e = ev(wait_hours=2, requires_overnight=False, disruption_type="delay")
    assert not e.compensation_eligible
    assert "threshold not met" in e.rule_reasons["compensation"]


def test_cancellation_always_requires_reaccommodation():
    """DOC-09: a cancellation is always re-accommodated, however short the wait."""
    assert ev(wait_hours=0.5, requires_overnight=False).reaccommodation_required


def test_eu_route_is_flagged_not_suppressed():
    e = ev(origin_airport="GRU", destination_airport="MAD")
    assert "EU261 may apply" in e.rule_reasons["eu261"]


def test_non_brazilian_origin_is_unsupported():
    with pytest.raises(ValueError, match="ANAC 400"):
        ev(origin_airport="SCL", destination_airport="MAD")


def test_negative_wait_rejected():
    with pytest.raises(ValueError, match="wait_hours"):
        ev(wait_hours=-1)
