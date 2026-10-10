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

## concierge — `/mcp/concierge/` (partner hotels, transfers, vouchers)
| Tool | Args | Result |
|---|---|---|
| `hotel.book` | `hotel_id`, `nights ≥ 1`, `pax ≥ 1`, `passenger_id?` | `{booking_ref, hotel_id, nights, pax, passenger_id, at}`. Only hotels in the world's `hotels_with_rooms` (HT8842) succeed; otherwise a tool error. Stored in `sim.partners.hotel_bookings`. |
| `transfer.book` | `origin`, `destination`, `at` (aware datetime) | `{transfer_ref, origin, destination, pickup_at}` (the brief's `from`/`to` are `origin`/`destination`). |
| `voucher.issue` | `type` (`meal`/`hotel`/`transport`), `amount` (USD, 0 < x ≤ 10000) | `{voucher_id, type, amount, currency}`. |

## whatsapp — `/mcp/whatsapp/`
| Tool | Args | Result |
|---|---|---|
| `message.send` | `to` (passenger id), `body`, `buttons?` | The stored outbound message `{message_id, passenger_id, direction, body, buttons, at}`. |
| `message.transcript` | `to` | `{messages: [...]}`, both directions, in order. |
| `message.receive` | `sender`, `body` | Inbound hook (API/UI/tests): stored in the transcript, appended to the inbound log and passed to in-process listeners (`sim.partners.add_inbound_listener`). |
| `message.inbound` | `cursor = 0`, `timeout_s = 0` (max 60) | `{messages, next_cursor}`. Long-poll of inbound messages for remote subscribers such as the Customer runtime. |

## payments — `/mcp/payments/`
| Tool | Args | Result |
|---|---|---|
| `charge` | `pnr`, `amount` (USD-scale, > 0), `currency` (`^[A-Z]{3}$`), `idempotency_key` | `{charge_id, pnr, amount, currency, status: "succeeded", at, idempotent_replay}`. Repeating a key with identical parameters returns the same charge (`idempotent_replay: true`); a different amount/PNR/currency under the same key is a tool error. Unknown PNR is an error. |

## dcs — `/mcp/dcs/` (departure control)
Seat state is shared with the world: `seat.assign` writes the seat to the PNR and removes it from
the flight's open seats. Holds live in `acme.sim.dcs.DcsState` (`dcs_state(sim)`).
| Tool | Args | Result |
|---|---|---|
| `seatmap.get` | `flight` | `{flight, seats: {cabin: [open seats, holds excluded]}, held: {seat: pnr}}`. |
| `seat.hold` | `flight`, `seat`, `pnr` | `{flight, seat, pnr, status: "held"}`. Re-holding by the same PNR is idempotent; a hold by another PNR, or of a seat that is not open, is a tool error. |
| `seat.assign` | `flight`, `seat`, `pnr`, `cabin` | `{flight, seat, pnr, cabin, status: "assigned"}`. The seat must be open in that cabin and free or held by the same PNR. Clears the hold; replaces the PNR's seat on that flight. |
| `boarding_pass.issue` | `pnr`, `flight` | `{boarding_pass_id, pnr, passenger_name, flight, origin, destination, departure, seat, cabin}`. The PNR must have a segment on `flight` (rebook first) and an assigned seat there. |

## worldtracer — `/mcp/worldtracer/` (baggage)
| Tool | Args | Result |
|---|---|---|
| `bag.get` | `tag` | `{tag, pnr, routing}`. Routing defaults to the PNR's first segment flight. |
| `bag.reroute` | `tag`, `flight` | The updated bag. Rerouting onto a cancelled or unknown flight is a tool error. |

## loyalty — `/mcp/loyalty/` (Loyalty/FFP)
Access (enforced by the allowlist, see Access control below): `customer` is allowed,
`sales` is denied (403), because tier and benefits are passenger data.
| Tool | Args | Result |
|---|---|---|
| `member.get` | `passenger_id` | `{passenger_id, member_id, tier, status_miles, benefits}`. Mariana: Diamond, 250000 miles. |
| `courtesy.eligibility` | `passenger_id`, `kind` (`upgrade_recovery`) | `{passenger_id, kind, tier, eligible, discount_pct, rule}`. Eligible iff the tier is in `margin_opt.RECOVERY_DISCOUNT_PCT` (Diamond, Gold); `rule` references that table. |

## Access control (governance layer 1)
`acme-sim` is deny-by-default (`acme.sim.auth.AccessControlMiddleware`, wraps the whole app):
- Every request needs `Authorization: Bearer <token>`. Missing or unknown token: **401**.
- The token maps to a caller. The caller's allowlist is checked **before routing**: a path outside it
  gets **403** whether or not a server is mounted there (`/mcp/doesnotexist` included). Paths with
  `..`, `//` or `%` are refused for allowlisted callers, and the server name must match exactly.

| Caller | Servers |
|---|---|
| `customer` | all, including read-only `inventory` (and `loyalty`) |
| `sales` | `inventory` only |
| `test-admin` | all (tests/demo only) |

Tokens: `ACME_SIM_TOKENS="customer=<tok>,sales=<tok>,test-admin=<tok>"` (≥ 12 chars, distinct, known
callers only). If unset, random per-process tokens are generated and **not printed**; set the variable
to use the sim from another process. Tokens are never committed, logged or echoed (tested).
`create_app(sim, tokens=...)` takes tokens explicitly (tests). Clients build their MCP config with
`acme.sim.auth.client_config(caller, base_url, tokens, servers)`, which returns only the servers the
caller may use, each with that caller's header. The Customer runtime already uses it (`sim.client.inprocess_connector` / `http_connector` act as one
caller with only that caller's token); Sales (#14) must do the same with the `sales` credential.
