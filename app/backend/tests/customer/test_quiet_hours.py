from datetime import datetime, time

import pytest

from acme.customer.quiet_hours import QuietHours, can_send_proactive
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT, GRU
from acme.domain.journey import DisruptionStatus as S
from acme.domain.journey import JourneyState


@pytest.mark.parametrize(
    ("t", "inside"),
    [(time(22), True), (time(23, 59), True), (time(0), True), (time(6, 59), True),
     (time(7), False), (time(12), False), (time(21, 59), False)],
)  # fmt: skip
def test_default_window_22_to_07(t, inside):
    assert QuietHours().contains(t) is inside


def test_configurable_non_wrapping_window():
    qh = QuietHours(start=time(13), end=time(15))
    assert qh.contains(time(14)) and not qh.contains(time(15)) and not qh.contains(time(12))


def journey_at(status_path):
    j = JourneyState("J-0a1b2c3d", FixedClock(DISRUPTION_DETECTED_AT))
    for s in status_path:
        j.transition(s)
    return j


NIGHT = DISRUPTION_DETECTED_AT
DAY = datetime(2026, 10, 10, 9, 0, tzinfo=GRU)


def test_quiet_hours_block_proactive_until_first_resolved():
    resolving = journey_at([S.RESOLVING])
    assert not can_send_proactive(resolving, NIGHT)
    resolved = journey_at([S.RESOLVING, S.RESOLVED])
    assert can_send_proactive(resolved, NIGHT)


def test_replanning_after_resolved_still_counts_as_resolved_once():
    j = journey_at([S.RESOLVING, S.RESOLVED, S.REPLANNING])
    assert can_send_proactive(j, NIGHT)


def test_daytime_is_unrestricted_and_passenger_initiated_always_allowed():
    assert can_send_proactive(journey_at([S.RESOLVING]), DAY)
    assert can_send_proactive(journey_at([S.RESOLVING]), NIGHT, passenger_initiated=True)


def test_naive_time_rejected():
    with pytest.raises(ValueError):
        can_send_proactive(journey_at([]), datetime(2026, 10, 9, 23))
