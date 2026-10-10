from uuid import uuid4

import pytest

from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT
from acme.domain.journey import DisruptionStatus as S
from acme.domain.journey import JourneyState, PassengerRequest
from acme.domain.models import Offer
from acme.memory import JourneyStore, SqliteJourneyStore


def journey():
    j = JourneyState("J-0a1b2c3d", FixedClock(DISRUPTION_DETECTED_AT), passenger_tier="diamond",
                     current_flight="AC1252")  # fmt: skip
    j.append_event("occ", "cancellation_detected", {"flight": "AC1234", "reason": "x"})
    j.transition(S.RESOLVING)
    j.transition(S.RESOLVED)
    j.resolution_acknowledged = True
    j.open_requests.append(PassengerRequest(request_id="r1", text="after 10", resolved=True))
    rec = j.add_offer(Offer(
        offer_id=uuid4(), journey_id="J-0a1b2c3d", flight="AC1252", cabin="premium_business",
        price="135", standard_price="290", net_margin="80", currency="USD",
        origin="sales", optional=True,
    ))  # fmt: skip
    rec.status = "accepted"
    return j


@pytest.fixture(params=["memory", "file"])
def store(request, tmp_path):
    path = ":memory:" if request.param == "memory" else str(tmp_path / "journeys.db")
    s = SqliteJourneyStore(path)
    yield s
    s.close()


def test_round_trip_is_identical(store):
    j = journey()
    store.save(j)
    back = store.load("J-0a1b2c3d", FixedClock(DISRUPTION_DETECTED_AT))
    assert back.timeline == j.timeline
    assert (back.status, back.resolution_acknowledged) == (j.status, True)
    assert (back.passenger_tier, back.current_flight) == ("diamond", "AC1252")
    assert [(r.status, r.offer) for r in back.offers] == [(r.status, r.offer) for r in j.offers]
    assert back.open_requests == j.open_requests


def test_restored_journey_keeps_working(store):
    store.save(journey())
    back = store.load("J-0a1b2c3d", FixedClock(DISRUPTION_DETECTED_AT))
    back.transition(S.CLOSED)
    assert back.timeline[-1].type == "status_changed"


def test_save_overwrites_and_unknown_is_none(store):
    j = journey()
    store.save(j)
    j.transition(S.CLOSED)
    store.save(j)
    assert store.load("J-0a1b2c3d").status == S.CLOSED
    assert store.load("J-nope0000") is None
    assert store.ids() == ["J-0a1b2c3d"]


def test_file_store_survives_reopen(tmp_path):
    path = str(tmp_path / "j.db")
    a = SqliteJourneyStore(path)
    a.save(journey())
    a.close()
    b = SqliteJourneyStore(path)
    assert b.load("J-0a1b2c3d").status == S.RESOLVED
    b.close()


def test_port_is_a_protocol():
    assert isinstance(SqliteJourneyStore(":memory:"), JourneyStore)
