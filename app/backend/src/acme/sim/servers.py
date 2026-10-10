"""One FastMCP server per simulated system (catalogue: docs/product/simulators.md)."""

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AwareDatetime

from acme.sim.world import SimWorld

# The sim listens on loopback / in-process only; auth arrives as middleware in #27.
_SECURITY = TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _server(name: str) -> FastMCP:
    # streamable_http_path="/" so the server can be mounted at /mcp/<name>.
    return FastMCP(name, streamable_http_path="/", transport_security=_SECURITY)


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def build_occ(sim: SimWorld) -> FastMCP:
    mcp = _server("occ")

    @mcp.tool(name="flight_ops.subscribe")
    async def subscribe(cursor: int = 0, timeout_s: float = 0) -> dict[str, Any]:
        """Ops events with index >= cursor; waits up to timeout_s for new ones (long-poll)."""
        events, next_cursor = await sim.events_after(cursor, min(timeout_s, 60))
        return {"events": events, "next_cursor": next_cursor}

    @mcp.tool(name="flight_ops.publish_cancellation")
    async def publish_cancellation(flight: str, reason: str) -> dict[str, Any]:
        """Cancel a flight and publish the event (used by tests/demo to trigger scenario 1)."""
        return await sim.publish_cancellation(flight, reason)

    return mcp


def build_pss(sim: SimWorld) -> FastMCP:
    mcp = _server("pss")

    @mcp.tool(name="pnr.get")
    def pnr_get(pnr: str) -> dict[str, Any]:
        """Return a PNR: segments, seats, bags."""
        return _dump(sim.get_pnr(pnr))

    @mcp.tool(name="flights.get")
    def flights_get(flight: str) -> dict[str, Any]:
        """Return one flight with its scheduled times and status."""
        return _dump(sim.flight(flight))

    @mcp.tool(name="flight.manifest")
    def flight_manifest(flight: str) -> dict[str, Any]:
        """PNRs (locator + passenger id) holding a segment on the flight."""
        sim.flight(flight)
        return {
            "flight": flight,
            "pnrs": [
                {"pnr": p.locator, "passenger_id": p.passenger_id}
                for p in sim.world.pnrs.values()
                if flight in {s.flight_number for s in p.segments}
            ],
        }

    @mcp.tool(name="flights.search")
    def flights_search(
        origin: str,
        dest: str,
        after: AwareDatetime | None = None,
        before: AwareDatetime | None = None,
    ) -> dict[str, Any]:
        """Non-cancelled flights origin->dest departing within [after, before]."""
        return {"flights": [_dump(f) for f in sim.search_flights(origin, dest, after, before)]}

    @mcp.tool(name="pnr.rebook")
    async def pnr_rebook(pnr: str, flight: str) -> dict[str, Any]:
        """Move the PNR's segment on the same route to `flight`."""
        return _dump(await sim.rebook(pnr, flight))

    return mcp


def build_crm(sim: SimWorld) -> FastMCP:
    mcp = _server("crm")

    @mcp.tool(name="profile.get")
    def profile_get(passenger_id: str) -> dict[str, Any]:
        """Passenger profile including loyalty."""
        return _dump(sim.get_passenger(passenger_id))

    @mcp.tool(name="interaction.log")
    def interaction_log(passenger_id: str, channel: str, summary: str) -> dict[str, Any]:
        """Record a customer interaction."""
        return {"interaction_id": sim.log_interaction(passenger_id, channel, summary)}

    return mcp


def build_inventory(sim: SimWorld) -> FastMCP:
    """Read-only, PII-free: the only server the Sales agent may reach (enforced in #27)."""
    mcp = _server("inventory")

    @mcp.tool(name="cabin_inventory.get")
    def cabin_inventory_get(flight: str, cabin: str) -> dict[str, Any]:
        """Open-seat count only."""
        inv = sim.cabin_inventory(flight, cabin)
        return {"flight": inv.flight_number, "cabin": inv.cabin, "open_seats": inv.open_seats}

    @mcp.tool(name="upgrade_fare.get")
    def upgrade_fare_get(flight: str, cabin: str) -> dict[str, Any]:
        """Standard upgrade fare and cost floor (USD)."""
        inv = sim.cabin_inventory(flight, cabin)
        return {
            "flight": inv.flight_number,
            "cabin": inv.cabin,
            "currency": "USD",
            "standard_fare": sim.money(inv.standard_upgrade_fare),
            "cost_floor": sim.money(inv.cost_floor),
        }

    return mcp
