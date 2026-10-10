from datetime import timedelta

import pytest

from acme.domain.clock import FixedClock, SystemClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT


def test_fixed_clock_returns_instant():
    clock = FixedClock(DISRUPTION_DETECTED_AT)
    assert clock.now() == DISRUPTION_DETECTED_AT
    assert (clock.now().hour, clock.now().minute) == (22, 3)


def test_fixed_clock_advance_and_naive_rejected():
    clock = FixedClock(DISRUPTION_DETECTED_AT)
    clock.advance(timedelta(minutes=5))
    assert clock.now() == DISRUPTION_DETECTED_AT + timedelta(minutes=5)
    with pytest.raises(ValueError):
        FixedClock(DISRUPTION_DETECTED_AT.replace(tzinfo=None))


def test_system_clock_is_aware():
    assert SystemClock().now().tzinfo is not None
