from datetime import datetime
from uuid import uuid4

import pytest

from acme.customer.runtime import CustomerRuntime
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT, GRU
from acme.domain.journey import DisruptionStatus as S
from acme.domain.models import Offer
from acme.llm.fake import FakeLlm
from acme.sim.dcs import dcs_state

PID = "P-MARIANA"


@pytest.fixture
async def setup():
    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    rt.clock.advance(datetime(2026, 10, 9, 22, 9, tzinfo=GRU) - rt.clock.now())
    await rt.tick()
    return rt, rt.journeys[jid]


def of(rec, type_):
    return [e for e in rec.state.timeline if e.type == type_]


def sent(rt):
    return [m for m in rt.messages[PID] if m.direction == "outbound"]


async def test_after_10am_replans_to_ac1252_and_returns_to_resolved(setup):
    rt, rec = setup
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    st = rec.state
    assert st.status == S.RESOLVED and st.open_requests == []
    assert st.resolution_acknowledged is False
    statuses = [e.payload["to"] for e in of(rec, "status_changed")]
    assert statuses[-2:] == ["REPLANNING", "RESOLVED"]
    assert of(rec, "rebooked")[-1].payload["flight"] == "AC1252"
    assert of(rec, "rebooked")[-1].payload["departure"].startswith("2026-10-10T11:40")
    assert of(rec, "seat_held")[-1].payload["seat"] == "14A"
    assert of(rec, "seat_held")[-1].payload["flight"] == "AC1252"
    assert of(rec, "bag_rerouted")[-1].payload["flight"] == "AC1252"
    check = of(rec, "connection_verified")[-1].payload
    assert (check["flight"], check["feasible"], check["margin_minutes"]) == ("AC1252", True, 10)
    pnr = rt.world.pnrs["MSLV42"]
    assert [s.flight_number for s in pnr.segments] == ["AC1252", "AC0800"]
    assert [(s.flight_number, s.seat) for s in pnr.seats] == [("AC1252", "14A")]
    assert dcs_state(rt.sim).bag("BAG0001")["routing"] == "AC1252"
    (closed,) = of(rec, "request_closed")
    assert closed.payload["outcome"] == "satisfied"


async def test_confirmation_message_invites_the_passenger_to_confirm(setup):
    rt, rec = setup
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    last = sent(rt)[-1]
    for fact in ("AC1252", "11:40", "14A"):
        assert fact in last.text
    assert "confirm" in last.text.lower()


async def test_transfer_to_airport_is_rescheduled_for_the_later_flight(setup):
    rt, rec = setup
    before = len(of(rec, "transfer_arranged"))
    assert before == 2  # the original airport<->hotel legs
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    new = of(rec, "transfer_arranged")[before:]
    assert len(new) == 1 and new[0].payload["leg"] == "hotel_to_airport"
    pickups = [t["pickup_at"] for t in rt.sim.partners.transfers.values()]
    assert any(p.startswith("2026-10-10T08:40") for p in pickups)  # 11:40 - 3h


async def test_then_ack_sets_resolution_acknowledged(setup):
    rt, rec = setup
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    await rt.handle_inbound(PID, "Perfect, thanks!")
    assert rec.state.resolution_acknowledged is True


async def test_unsatisfiable_request_keeps_booking_and_explains(setup):
    rt, rec = setup
    await rt.handle_inbound(PID, "Can I leave after 1pm?")
    st = rec.state
    assert st.status == S.RESOLVED and st.open_requests == []
    assert [s.flight_number for s in rt.world.pnrs["MSLV42"].segments][0] == "AC1240"
    (closed,) = of(rec, "request_closed")
    assert closed.payload["outcome"] == "unsatisfiable"
    text = sent(rt)[-1].text
    assert "AC1240" in text and "AC1260" in text and "connection" in text.lower()
    assert len(of(rec, "rebooked")) == 1  # only the original rebooking


async def test_request_already_satisfied_by_current_flight(setup):
    rt, rec = setup
    await rt.handle_inbound(PID, "Can I leave after 8am?")
    assert [s.flight_number for s in rt.world.pnrs["MSLV42"].segments][0] == "AC1240"
    assert of(rec, "request_closed")[0].payload["outcome"] == "satisfied"
    assert rec.state.status == S.RESOLVED and "AC1240" in sent(rt)[-1].text


async def test_live_offer_is_invalidated_by_replanning(setup):
    rt, rec = setup
    offer = rec.state.add_offer(
        Offer(
            offer_id=uuid4(),
            journey_id=rec.state.journey_id,
            flight="AC1252",
            cabin="premium_business",
            price="135",
            standard_price="290",
            net_margin="80",
            currency="USD",
            origin="sales",
            optional=True,
        )  # fmt: skip
    )
    offer.status = "approved"
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    assert offer.status == "invalidated"


ESCALATION = "I can't handle that automatically, so I've asked a colleague to follow up with you."


async def test_seat_failure_needs_human_and_keeps_replanning(setup):
    rt, rec = setup
    rt.world.flights["AC1252"].open_seats["economy"] = ["14B"]
    before = len(sent(rt))
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    assert of(rec, "needs_human")
    assert rec.state.status == S.REPLANNING and len(rec.state.open_requests) == 1
    assert [m.text for m in sent(rt)[before:]] == [ESCALATION]  # never silent (#398)


async def test_rebook_race_escalation_is_answered_on_whatsapp(setup, monkeypatch):
    from acme.customer.specialists.irrops import IrropsRebooking, RebookResponse

    async def raced(self, req, emit):
        emit("needs_human", {"step": "rebook", "reason": "raced"})
        return RebookResponse(status="no_feasible_flight")

    monkeypatch.setattr(IrropsRebooking, "handle", raced)
    rt, rec = setup
    before = len(sent(rt))
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    assert [m.text for m in sent(rt)[before:]] == [ESCALATION]
    assert rec.state.status == S.REPLANNING and len(rec.state.open_requests) == 1


async def test_rescheduled_transfer_starts_at_the_booked_hotel(setup):
    rt, rec = setup
    rec.hotel = rec.hotel.model_copy(update={"hotel_id": "HT0001"})
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    origins = {t["origin"] for t in rt.sim.partners.transfers.values() if t["origin"] != "GRU"}
    assert "HT0001" in origins


async def test_superseded_hotel_pickup_is_cancelled_once_the_new_one_is_booked(setup):
    rt, rec = setup
    old = next(
        e.payload["transfer_ref"]
        for e in of(rec, "transfer_arranged")
        if e.payload["leg"] == "hotel_to_airport"
    )
    assert old in rt.sim.partners.transfers
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    transfers = rt.sim.partners.transfers
    assert old not in transfers and len(transfers) == 2
    assert any(t["pickup_at"].startswith("2026-10-10T08:40") for t in transfers.values())
    assert any(t["destination"] == "HT8842" for t in transfers.values())  # airport_to_hotel kept


async def test_every_event_of_the_full_replanning_flow_matches_the_api_contract(setup):
    from pydantic import TypeAdapter

    from acme.api.events import TimelineEvent

    rt, rec = setup
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    await rt.handle_inbound(PID, "Perfect, thanks!")
    adapter = TypeAdapter(TimelineEvent)
    for e in rec.state.timeline:
        adapter.validate_python(e.model_dump(mode="json"))


async def test_ac3_replan_honours_the_worlds_mct(setup):
    rt, rec = setup
    rt.world.mct_minutes = 120  # AC1252 lands 12:20, AC0800 leaves 14:00: 100 < 120
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    closed = of(rec, "request_closed")[-1]
    assert closed.payload["outcome"] == "unsatisfiable"
    assert [s.flight_number for s in rt.world.pnrs["MSLV42"].segments] == ["AC1240", "AC0800"]


async def test_replan_with_preferred_seat_taken_explains_courtesy_window_seat(setup):
    rt, rec = setup
    rt.world.flights["AC1252"].open_seats["economy"] = ["15A"]
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    assert rec.state.status == S.RESOLVED
    last = sent(rt)[-1].text
    assert "nearest window seat 15A (14A was unavailable)" in last
    assert all(k in last for k in ("AC1252", "11:40", "15A", "14A", "window"))


# --- the preferred seat survives a courtesy substitution and a voided upgrade (#491) -------------
async def seat_taken_setup(rt_clock=DISRUPTION_DETECTED_AT):
    """seat-taken: 14A is gone on AC1240, so Mariana gets the courtesy window seat 15A there."""
    rt = CustomerRuntime(FixedClock(rt_clock), llm=FakeLlm())
    rt.world.flights["AC1240"].open_seats["economy"] = ["15A"]
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    rec = rt.journeys[jid]
    assert [s.seat for s in rt.world.pnrs["MSLV42"].seats] == ["15A"]
    assert of(rec, "seat_held")[-1].payload["requested"] == "14A"
    rt.clock.advance(datetime(2026, 10, 9, 22, 9, tzinfo=GRU) - rt.clock.now())
    await rt.tick()
    return rt, rec


async def test_replan_after_a_courtesy_seat_asks_for_the_original_seat_not_the_substitute():
    rt, rec = await seat_taken_setup()
    await rt.handle_inbound(PID, "Can I leave after 10am?")  # 14A is open on AC1252
    held = of(rec, "seat_held")[-1].payload
    assert (held["flight"], held["seat"], held["requested"]) == ("AC1252", "14A", "14A")
    assert held["reason"] == "kept seat 14A on AC1252"
    last = sent(rt)[-1].text
    assert (
        last == "All set: you are now on AC1252, departing at 11:40, seat 14A, your bag is "
        "re-routed and your Santiago connection is protected. Please confirm that this works "
        "for you."
    )
    assert "15A was unavailable" not in last and "nearest window seat" not in last


async def test_secondary_cancellation_after_a_courtesy_seat_asks_for_the_original_seat():
    rt, rec = await seat_taken_setup()
    await rt.trigger_cancellation("AC1240", "weather")  # rebooked on AC1252, where 14A is open
    held = of(rec, "seat_held")[-1].payload
    assert (held["flight"], held["seat"], held["requested"]) == ("AC1252", "14A", "14A")
    assert held["reason"] == "kept seat 14A on AC1252"
    assert [s.seat for s in rt.world.pnrs["MSLV42"].seats] == ["14A"]


async def test_secondary_cancellation_after_a_voided_upgrade_asks_for_the_original_seat():
    from acme.domain.models import SeatAssignment

    rt, rec = await seat_taken_setup()
    # a paid premium seat stands in the PNR on AC1240 (the upgrade the disruption then voids)
    rt.world.pnrs["MSLV42"].seats = [
        SeatAssignment(flight_number="AC1240", seat="2A", cabin="premium_business")
    ]
    await rt.trigger_cancellation("AC1240", "weather")
    held = of(rec, "seat_held")[-1].payload
    assert (held["flight"], held["seat"], held["requested"]) == ("AC1252", "14A", "14A")


async def test_when_the_original_seat_is_gone_too_the_request_is_still_for_the_original():
    rt, rec = await seat_taken_setup()
    rt.world.flights["AC1252"].open_seats["economy"] = ["15A"]  # 14A is taken on AC1252 as well
    await rt.handle_inbound(PID, "Can I leave after 10am?")
    held = of(rec, "seat_held")[-1].payload
    assert (held["seat"], held["requested"]) == ("15A", "14A")
    assert "nearest window seat 15A (14A was unavailable)" in sent(rt)[-1].text


async def test_a_journey_without_history_falls_back_to_the_seat_on_the_flight():
    from acme.customer.specialists.seat import preferred_seat

    rt = CustomerRuntime(FixedClock(DISRUPTION_DETECTED_AT), llm=FakeLlm())
    (jid,) = await rt.trigger_cancellation("AC1234", "unscheduled maintenance")
    rec = rt.journeys[jid]
    assert preferred_seat(rec, "9C") == "14A"  # the history wins over the PNR seat
    rec.state._timeline[:] = [  # noqa: SLF001
        e for e in rec.state.timeline if e.type not in ("profile_loaded", "seat_held")
    ]
    assert preferred_seat(rec, "9C") == "9C"  # no history: the seat on the flight
    assert preferred_seat(rec, None) is None
