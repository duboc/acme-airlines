# Simulated enterprise systems (MCP)

`uv run acme-sim` (from `app/backend`) serves every simulator from one ASGI app over one
shared, seeded world (`acme.sim.world.SimWorld`, built from `acme.domain.fixtures.mariana_world()`).
Port: `ACME_SIM_PORT` (default `8700`), bound to `127.0.0.1`. Transport: MCP streamable HTTP at
`/mcp/<server>/`. Servers are registered in `acme.sim.app.DEFAULT_REGISTRY` (`name -> factory`);
`create_app(sim, registry=...)` mounts any registry. Errors (unknown flight/PNR, ...) are returned as
MCP tool errors. Times are timezone-aware ISO 8601.

## occ — `/mcp/occ/`
| Tool | Args | Result |
|---|---|---|
| `flight_ops.subscribe` | `cursor: int = 0`, `timeout_s: float = 0` (max 60) | `{events: [...], next_cursor}`. Long-poll: returns events with index ≥ cursor, waiting up to `timeout_s` if none. Pass `next_cursor` on the next call. |
| `flight_ops.publish_cancellation` | `flight`, `reason` | The event `{type: "flight_cancelled", flight, reason, at}`; also sets the flight status to `cancelled`. |

## pss — `/mcp/pss/`
| Tool | Args | Result |
|---|---|---|
| `pnr.get` | `pnr` | PNR: `locator`, `passenger_id`, `segments`, `seats`, `bags`. |
| `flights.get` | `flight` | One flight with scheduled times and status. |
| `flight.manifest` | `flight` | `{flight, pnrs: [{pnr, passenger_id}]}` for PNRs holding a segment on the flight. |
| `flights.search` | `origin`, `dest`, `after?`, `before?` (aware datetimes, bound departure) | `{flights: [Flight...]}`, non-cancelled, by departure. |
| `pnr.rebook` | `pnr`, `flight` | Updated PNR. Replaces the segment on the same route; releases the old seat; keeps bags. |

## crm — `/mcp/crm/`
| Tool | Args | Result |
|---|---|---|
| `profile.get` | `passenger_id` | Passenger with tier and loyalty. |
| `interaction.log` | `passenger_id`, `channel`, `summary` | `{interaction_id}`. |

## inventory — `/mcp/inventory/` (read-only, PII-free; the only server Sales may reach, enforced in #27)
| Tool | Args | Result |
|---|---|---|
| `cabin_inventory.get` | `flight`, `cabin` | `{flight, cabin, open_seats}` only. |
| `upgrade_fare.get` | `flight`, `cabin` | `{flight, cabin, currency: "USD", standard_fare, cost_floor}` (decimal strings). |
