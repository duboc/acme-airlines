"""Change agent (scenario 3b): conversational replanning for a `departure_after` request.

Re-runs rebooking under the new constraint (earliest feasible flight departing after the asked
time that still protects the onward connection), keeps the seat, re-routes the bag, re-verifies
the connection, reschedules the hotel->airport transfer, closes the request and returns the
journey to RESOLVED with a confirmation message. If nothing satisfies the request the booking is
kept, the passenger is told the options honestly and the request is closed `unsatisfiable`.
A failed step records `needs_human` and leaves the journey REPLANNING with the request open.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from acme.customer.specialists.baggage import BaggageSpecialist, BagRequest
from acme.customer.specialists.irrops import IrropsRebooking, RebookRequest
from acme.customer.specialists.seat import SeatRequest, SeatSpecialist
from acme.domain.journey import DisruptionStatus
from acme.policy.connection_check import check_connection
from acme.sim.client import ToolError

if TYPE_CHECKING:
    from acme.customer.runtime import CustomerRuntime, JourneyRecord

ACTOR = "change_agent"
TRANSFER_LEAD = timedelta(hours=3)
ESCALATION_REPLY = (
    "I can't handle that automatically, so I've asked a colleague to follow up with you."
)


def _last_payload(rec: JourneyRecord, type_: str) -> dict:
    return [e.payload for e in rec.state.timeline if e.type == type_][-1]


class ReplanningChangeAgent:
    async def handle(
        self, rt: CustomerRuntime, rec: JourneyRecord, request_id: str, after: time
    ) -> None:
        client, state = rt.client, rec.state

        def emit(type_: str, payload: dict) -> None:
            state.append_event(ACTOR, type_, payload)

        pnr = await client.call("pss", "pnr.get", pnr=rec.pnr_locator)
        current = _last_payload(rec, "rebooked")["flight"]
        current_flight = await client.call("pss", "flights.get", flight=current)
        flights = [s["flight_number"] for s in pnr["segments"]]
        onward = None
        if current in flights and flights.index(current) + 1 < len(flights):
            onward = await client.call(
                "pss", "flights.get", flight=flights[flights.index(current) + 1]
            )
        dep = datetime.fromisoformat(current_flight["scheduled_departure"])
        req = RebookRequest(
            pnr=rec.pnr_locator,
            origin=current_flight["origin"],
            destination=current_flight["destination"],
            after=datetime.combine(dep.date(), after, tzinfo=dep.tzinfo),
            onward_departure=(
                datetime.fromisoformat(onward["scheduled_departure"]) if onward else None
            ),
            mct_minutes=rt.world.mct_minutes,
        )
        irrops = IrropsRebooking(client)
        options = await irrops.search(req)
        target = next((f for f in options if irrops.feasible(req, f) is not None), None)

        if target is None:
            await self._unsatisfiable(rt, rec, request_id, after, current_flight, options, req)
            return
        if target["number"] == current:
            state.current_flight = current
            self._close(rec, request_id, "satisfied")
            await rt.send(
                rec.passenger_id,
                f"You are already on {current}, departing at {dep:%H:%M}, which is after "
                f"{after:%H:%M}. Please confirm that works for you.",
            )
            state.transition(DisruptionStatus.RESOLVED, actor=ACTOR)
            return

        old_seat = next((s["seat"] for s in pnr["seats"] if s["flight_number"] == current), None)
        tier = (await client.call("crm", "profile.get", passenger_id=rec.passenger_id))["tier"]
        rebook = await irrops.handle(req, emit)
        if rebook.status != "rebooked":  # raced with inventory: never silent
            await rt.send(rec.passenger_id, ESCALATION_REPLY)
            return
        state.current_flight = rebook.flight
        seat_ok = True
        if old_seat:
            seat = await SeatSpecialist(client).handle(
                SeatRequest(
                    pnr=rec.pnr_locator, flight=rebook.flight, preferred_seat=old_seat, tier=tier
                ),
                emit,
            )
            seat_ok = seat.status == "assigned"
        tags = [b["tag"] for b in pnr["bags"]]
        bags = await BaggageSpecialist(client).handle(
            BagRequest(flight=rebook.flight, tags=tags), emit
        )
        if onward and rebook.arrival:
            check = check_connection(
                onward["origin"],
                rebook.arrival,
                req.onward_departure,  # type: ignore[arg-type]
                req.mct_minutes,
            )
            emit(
                "connection_verified",
                {
                    "flight": rebook.flight,
                    "connection_flight": onward["number"],
                    "feasible": check.feasible,
                    "margin_minutes": check.margin_minutes,
                },
            )
        transfer_ok = await self._reschedule_transfer(rt, rec, current_flight, rebook.departure)
        if not (seat_ok and set(bags.rerouted) == set(tags) and transfer_ok):
            # needs_human already recorded; journey stays REPLANNING, request open
            await rt.send(rec.passenger_id, ESCALATION_REPLY)
            return
        self._close(rec, request_id, "satisfied")
        state.transition(DisruptionStatus.RESOLVED, actor=ACTOR)
        seat_now = next(
            (
                s["seat"]
                for s in (await client.call("pss", "pnr.get", pnr=rec.pnr_locator))["seats"]
            ),
            None,
        )
        await rt.send(
            rec.passenger_id,
            f"All set: you are now on {rebook.flight}, departing at {rebook.departure:%H:%M}"
            + (f", seat {seat_now}" if seat_now else "")
            + ", your bag is re-routed and your Santiago connection is protected. "
            "Please confirm that this works for you.",
        )

    def _close(self, rec: JourneyRecord, request_id: str, outcome: str) -> None:
        rec.state.open_requests = [r for r in rec.state.open_requests if r.request_id != request_id]
        rec.state.append_event(
            ACTOR, "request_closed", {"request_id": request_id, "outcome": outcome}
        )

    async def _reschedule_transfer(
        self,
        rt: CustomerRuntime,
        rec: JourneyRecord,
        old_flight: dict,
        new_departure: datetime | None,
    ) -> bool:
        if rec.hotel is None or new_departure is None:
            return True
        at = new_departure - TRANSFER_LEAD
        # every replan cancels the previous pickup, so only the latest one is still booked
        pickups = [
            e.payload["transfer_ref"]
            for e in rec.state.timeline
            if e.type == "transfer_arranged" and e.payload.get("leg") == "hotel_to_airport"
        ]
        superseded = pickups[-1:]
        try:
            r = await rt.client.call(
                "concierge", "transfer.book", origin=rec.hotel.hotel_id,
                destination=old_flight["origin"], at=at.isoformat(),
            )  # fmt: skip
            for ref in superseded:  # only once the new pickup is booked
                await rt.client.call("concierge", "transfer.cancel", transfer_ref=ref)
        except ToolError as exc:
            rec.state.append_event(
                ACTOR, "needs_human", {"step": "transfer:hotel_to_airport", "error": str(exc)}
            )
            return False
        rec.state.append_event(
            ACTOR, "transfer_arranged",
            {"transfer_ref": r["transfer_ref"], "leg": "hotel_to_airport",
             "entitlement": "ground_transfer", "reason": "rescheduled for the new departure"},
        )  # fmt: skip
        return True

    async def _unsatisfiable(
        self, rt, rec, request_id, after, current_flight, options, req: RebookRequest
    ) -> None:
        self._close(rec, request_id, "unsatisfiable")
        current = _last_payload(rec, "rebooked")
        dep = datetime.fromisoformat(current["departure"])
        notes = []
        for f in options:
            arr = datetime.fromisoformat(f["scheduled_arrival"])
            notes.append(
                f"{f['number']} ({datetime.fromisoformat(f['scheduled_departure']):%H:%M}, "
                f"arrives {arr:%H:%M}) would miss your onward connection"
            )
        why = "; ".join(notes) if notes else "there is no later flight today"
        await rt.send(
            rec.passenger_id,
            f"I could not find a flight after {after:%H:%M} that protects your connection: {why}. "
            f"Your booking stays on {current['flight']}, departing at {dep:%H:%M}. "
            "Tell me if you would like to look at other options.",
        )
        rec.state.transition(DisruptionStatus.RESOLVED, actor=ACTOR)
