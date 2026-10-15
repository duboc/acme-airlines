"""`rights.assess` measures the wait from its real start (#396)."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest

from acme.customer.specialists import rights

BRT = timezone(timedelta(hours=-3))


def _assess(detected: datetime, original: datetime, new: datetime, **kw):
    return rights.assess(
        origin="GRU",
        destination="GIG",
        reason="unscheduled maintenance",
        detected_at=detected,
        original_departure=original,
        new_departure=new,
        **kw,
    )


def test_mariana_waits_from_the_cancellation_not_the_departure(monkeypatch) -> None:
    seen: dict = {}
    real = rights.evaluate_duty_of_care

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(rights, "evaluate_duty_of_care", spy)
    res = _assess(
        datetime(2026, 10, 9, 22, 3, tzinfo=BRT),
        datetime(2026, 10, 9, 23, 30, tzinfo=BRT),
        datetime(2026, 10, 10, 8, 15, tzinfo=BRT),
    )
    assert seen["wait_hours"] == pytest.approx(10.2)
    assert seen["wait_start"] == time(22, 3)
    assert seen["requires_overnight"] is True
    assert res.hotel_required


def test_pre_departure_cancellation_grants_the_dinner_voucher() -> None:
    # 22:03 -> 01:00 is 2.95 h (1.5 h from the 23:30 departure would be denied)
    res = _assess(
        datetime(2026, 10, 9, 22, 3, tzinfo=BRT),
        datetime(2026, 10, 9, 23, 30, tzinfo=BRT),
        datetime(2026, 10, 10, 1, 0, tzinfo=BRT),
    )
    assert res.meal_vouchers


def test_post_midnight_departure_cancelled_before_midnight_is_overnight() -> None:
    res = _assess(
        datetime(2026, 10, 9, 22, 3, tzinfo=BRT),
        datetime(2026, 10, 10, 0, 15, tzinfo=BRT),
        datetime(2026, 10, 10, 8, 15, tzinfo=BRT),
    )
    assert res.hotel_required
    assert res.ground_transfer_required
    assert len(res.meal_vouchers) >= 2  # dinner and breakfast


def test_detection_after_departure_starts_the_wait_at_departure() -> None:
    res = _assess(
        datetime(2026, 10, 9, 23, 45, tzinfo=BRT),
        datetime(2026, 10, 9, 23, 30, tzinfo=BRT),
        datetime(2026, 10, 10, 8, 15, tzinfo=BRT),
    )
    assert res.hotel_required
