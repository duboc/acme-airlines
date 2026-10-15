"""Simulated DCS (seat map, holds, assignment, boarding passes) and WorldTracer (bags).

State that the shared `SimWorld` does not model lives in `DcsState`, looked up per world with
`dcs_state(sim)`. Seat assignment writes through to the PNR and the flight's open seats, so
every simulator sees one world. Catalogue: docs/product/simulators.md.
"""

from __future__ import annotations

from typing import Any
from weakref import WeakKeyDictionary

from mcp.server.fastmcp import FastMCP

from acme.domain.models import PNR, Bag, SeatAssignment
from acme.sim.current import lazy, resolve
from acme.sim.servers import _server
from acme.sim.world import SimError, SimWorld

_CABINS = ("economy", "premium_business")


class DcsState:
    def __init__(self, sim: SimWorld) -> None:
        self._sim = sim
        self.holds: dict[tuple[str, str], str] = {}  # (flight, seat) -> pnr
        self.assigned: dict[tuple[str, str], tuple[str, str]] = {}  # (flight, seat) -> (pnr, cabin)
        self.boarding_passes: dict[str, dict[str, Any]] = {}
        self.bag_routing: dict[str, str] = {}

    def export_data(self) -> dict[str, Any]:
        """JSON-able state (shared snapshot, #200); tuple keys become lists."""
        return {
            "holds": [[f, s, pnr] for (f, s), pnr in self.holds.items()],
            "assigned": [[f, s, pnr, cabin] for (f, s), (pnr, cabin) in self.assigned.items()],
            "boarding_passes": self.boarding_passes,
            "bag_routing": self.bag_routing,
        }

    def import_data(self, data: dict[str, Any]) -> None:
        self.holds = {(f, s): pnr for f, s, pnr in data["holds"]}
        self.assigned = {(f, s): (pnr, cabin) for f, s, pnr, cabin in data["assigned"]}
        self.boarding_passes = dict(data["boarding_passes"])
        self.bag_routing = dict(data["bag_routing"])

    # --- seats ---
    def _open(self, flight: str) -> dict[str, list[str]]:
        return self._sim.flight(flight).open_seats  # type: ignore[return-value]

    def _cabin_of(self, flight: str, seat: str) -> str | None:
        return next((c for c, seats in self._open(flight).items() if seat in seats), None)

    def seatmap(self, flight: str) -> dict[str, Any]:
        held = {seat: pnr for (f, seat), pnr in self.holds.items() if f == flight}
        seats = {c: [s for s in v if s not in held] for c, v in self._open(flight).items()}
        return {"flight": flight, "seats": seats, "held": held}

    def hold(self, flight: str, seat: str, pnr: str) -> dict[str, Any]:
        self._sim.get_pnr(pnr)
        if self._cabin_of(flight, seat) is None:
            raise SimError(f"seat {seat} is not available on {flight}")
        owner = self.holds.setdefault((flight, seat), pnr)
        if owner != pnr:
            raise SimError(f"seat {seat} on {flight} is held by another booking")
        return {"flight": flight, "seat": seat, "pnr": pnr, "status": "held"}

    def assign(self, flight: str, seat: str, pnr: str, cabin: str) -> dict[str, Any]:
        record = self._sim.get_pnr(pnr)
        if cabin not in _CABINS:
            raise SimError(f"unknown cabin {cabin}")
        if self._cabin_of(flight, seat) != cabin:
            raise SimError(f"seat {seat} is not an open {cabin} seat on {flight}")
        owner = self.holds.get((flight, seat), pnr)
        if owner != pnr:
            raise SimError(f"seat {seat} on {flight} is held by another booking")
        self.holds.pop((flight, seat), None)
        self._open(flight)[cabin].remove(seat)  # type: ignore[attr-defined]
        record.seats = [s for s in record.seats if s.flight_number != flight]
        record.seats.append(SeatAssignment(flight_number=flight, seat=seat, cabin=cabin))  # type: ignore[arg-type]
        # A replaced seat stays blocked (still tracked here) until it is explicitly released.
        self.assigned[(flight, seat)] = (pnr, cabin)
        return {"flight": flight, "seat": seat, "pnr": pnr, "cabin": cabin, "status": "assigned"}

    def release(self, flight: str, seat: str, pnr: str) -> dict[str, Any]:
        """Free a seat this PNR holds or was assigned. Idempotent; only the holder may release."""
        record = self._sim.get_pnr(pnr)
        key = (flight, seat)
        hold_owner = self.holds.get(key)
        assignment = self.assigned.get(key)
        on_pnr = next(
            (s for s in record.seats if s.flight_number == flight and s.seat == seat), None
        )
        if hold_owner not in (None, pnr) or (assignment and assignment[0] != pnr):
            raise SimError(f"seat {seat} on {flight} belongs to another booking")
        result = {"flight": flight, "seat": seat, "pnr": pnr, "status": "released"}
        if hold_owner == pnr:
            del self.holds[key]  # a held seat never left the open list
            return result
        if assignment or on_pnr:
            cabin = assignment[1] if assignment else on_pnr.cabin  # type: ignore[union-attr]
            self.assigned.pop(key, None)
            record.seats = [s for s in record.seats if (s.flight_number, s.seat) != key]
            open_seats = self._open(flight).setdefault(cabin, [])  # type: ignore[arg-type]
            if seat not in open_seats:
                open_seats.append(seat)
            return result
        if self._cabin_of(flight, seat) is None:
            raise SimError(f"seat {seat} on {flight} is not held by booking {pnr}")
        return result  # already free: idempotent

    def boarding_pass(self, pnr: str, flight: str) -> dict[str, Any]:
        record: PNR = self._sim.get_pnr(pnr)
        if flight not in {s.flight_number for s in record.segments}:
            raise SimError(f"PNR {pnr} has no segment on {flight}")
        seat = next((s for s in record.seats if s.flight_number == flight), None)
        if seat is None:
            raise SimError(f"PNR {pnr} has no assigned seat on {flight}")
        f = self._sim.flight(flight)
        passenger = self._sim.get_passenger(record.passenger_id)
        bp = {
            "boarding_pass_id": f"BP-{len(self.boarding_passes) + 1:04d}",
            "pnr": pnr,
            "passenger_name": passenger.name,
            "flight": flight,
            "origin": f.origin,
            "destination": f.destination,
            "departure": f.scheduled_departure.isoformat(),
            "seat": seat.seat,
            "cabin": seat.cabin,
        }
        self.boarding_passes[bp["boarding_pass_id"]] = bp
        return bp

    # --- bags (WorldTracer) ---
    def _find_bag(self, tag: str) -> tuple[PNR, Bag]:
        for pnr in self._sim.world.pnrs.values():
            for bag in pnr.bags:
                if bag.tag == tag:
                    return pnr, bag
        raise SimError(f"unknown bag {tag}")

    def bag(self, tag: str) -> dict[str, Any]:
        pnr, _ = self._find_bag(tag)
        routing = self.bag_routing.get(tag, pnr.segments[0].flight_number)
        return {"tag": tag, "pnr": pnr.locator, "routing": routing}

    def reroute(self, tag: str, flight: str) -> dict[str, Any]:
        self._find_bag(tag)
        if self._sim.flight(flight).status == "cancelled":
            raise SimError(f"flight {flight} is cancelled")
        self.bag_routing[tag] = flight
        return self.bag(tag)


_STATES: WeakKeyDictionary[SimWorld, DcsState] = WeakKeyDictionary()


def dcs_state(sim: SimWorld) -> DcsState:
    if sim not in _STATES:
        _STATES[sim] = DcsState(sim)
    return _STATES[sim]


def build_dcs(sim: SimWorld) -> FastMCP:
    mcp = _server("dcs")
    st = lazy(lambda: dcs_state(resolve(sim)))  # per run, resolved at call time (#280)

    @mcp.tool(name="seatmap.get")
    def seatmap_get(flight: str) -> dict[str, Any]:
        """Open seats by cabin (excluding held ones) and current holds."""
        return st.seatmap(flight)

    @mcp.tool(name="seat.hold")
    def seat_hold(flight: str, seat: str, pnr: str) -> dict[str, Any]:
        """Hold an open seat for a PNR; fails if another PNR holds it."""
        return st.hold(flight, seat, pnr)

    @mcp.tool(name="seat.assign")
    def seat_assign(flight: str, seat: str, pnr: str, cabin: str) -> dict[str, Any]:
        """Assign an open seat (held by this PNR or free) and write it to the PNR."""
        return st.assign(flight, seat, pnr, cabin)

    @mcp.tool(name="seat.release")
    def seat_release(flight: str, seat: str, pnr: str) -> dict[str, Any]:
        """Release a seat held or assigned to this PNR back to inventory (idempotent)."""
        return st.release(flight, seat, pnr)

    @mcp.tool(name="boarding_pass.issue")
    def boarding_pass_issue(pnr: str, flight: str) -> dict[str, Any]:
        """Issue a boarding pass for an assigned seat."""
        return st.boarding_pass(pnr, flight)

    return mcp


def build_worldtracer(sim: SimWorld) -> FastMCP:
    mcp = _server("worldtracer")
    st = lazy(lambda: dcs_state(resolve(sim)))  # per run, resolved at call time (#280)

    @mcp.tool(name="bag.get")
    def bag_get(tag: str) -> dict[str, Any]:
        """Bag status: owning PNR and current routing flight."""
        return st.bag(tag)

    @mcp.tool(name="bag.reroute")
    def bag_reroute(tag: str, flight: str) -> dict[str, Any]:
        """Retag/route a bag onto another (non-cancelled) flight."""
        return st.reroute(tag, flight)

    return mcp
