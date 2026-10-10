# ACME Airlines application

- `backend/` — Python 3.12 uv project `acme` (agents, MCP sim servers, policy code).
  - `uv run acme-sim` starts the simulated MCP systems; see `docs/product/simulators.md`.

## Backend: lint and test locally

Prerequisite: [uv](https://docs.astral.sh/uv/).

```bash
cd app/backend
uv sync --frozen                 # install pinned deps (uv.lock)
uv run ruff check                # lint
uv run ruff format --check       # format check (use `uv run ruff format` to fix)
uv run pytest -m "not live"      # tests; `live` tests are deselected
```

Tests marked `@pytest.mark.live` call real services (e.g. Vertex AI) and never run by
default or in CI. Run them explicitly with `uv run pytest -m live`.

## LLM layer (`acme.llm`)

Agents depend on `LlmPort` (`compose_passenger_message`, `parse_passenger_request`) and get it
from `acme.llm.build_llm()`.

| Env var | Meaning |
|---------|---------|
| `LLM_MODE` | `fake` (default; deterministic, offline, used by dev/tests/CI) or `vertex` (live demo only). Anything else refuses to start. |
| `LLM_MODEL` | Optional model ID for `vertex`. Default `gemini-2.5-flash` (confirmed on the `global` endpoint from the factory VM; `gemini-2.0-flash` is not available there). |
| `GOOGLE_CLOUD_PROJECT` | Optional; must be `acme-airlines-factory` if set. |
| `GOOGLE_CLOUD_LOCATION` | Optional; must be `global` if set. Regional locations are refused. |

Vertex uses ADC only. Setting `GOOGLE_API_KEY` or `GEMINI_API_KEY` refuses to start. The hygiene
test `tests/unit/test_no_keys_no_regions.py` fails on API keys, regional endpoints or non-global
locations anywhere in `app/`.

Live smoke test (factory VM with ADC):

```bash
cd app/backend
LLM_MODE=vertex uv run pytest -m live tests/live
```

## Runtime API (`acme.api`)

FastAPI REST + SSE for the UI. Agent behaviour is stubbed (#17); the Customer agent work (#10-#16)
wires in real agents without changing the contract.

```bash
cd app/backend
uv run uvicorn acme.api.main:app --reload      # http://localhost:8000/docs
uv run python -m acme.api.openapi              # regenerate the committed contract
```

The contract is committed at `app/backend/openapi.json`; a test fails if `GET /openapi.json`
drifts from it, so regenerate and commit it whenever endpoints or models change. CORS allows the
Vite dev server (`http://localhost:5173`). `GET /journeys/{id}/events` is Server-Sent Events: one
`data:` frame (JSON `JourneyEvent`) per timeline event, replaying history first; optional
`?limit=N` closes the stream after N events.

## CI

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main`. The `backend`
job runs the four commands above. A `frontend` job is added with the frontend scaffold.

## Customer agent

`uv run acme-customer` (from `app/backend`) serves the runtime API on `ACME_CUSTOMER_PORT` (default 8800) with the
Customer agent and in-process simulators. `POST /scenario/trigger-cancellation` runs scenario 1
(silent ingestion and profile evaluation); see `docs/product/policy-rules.md` for the quiet-hours rule.

### Duty of care and RESOLVED (scenario 2b)

`acme.customer.specialists.run_duty_of_care(rt, rec, RebuildResult(...))` runs after the rebuild
(#11). The Rights & Compensation specialist runs `policy.duty_of_care` (wait = original -> new
departure, overnight = new departure on a later local day) and records `duty_of_care_assessed` with
entitlements and rule citations. The Concierge specialist fulfils them through the concierge MCP:
hotel HT8842, airport<->hotel transfers, meal vouchers. Each `hotel_booked` / `transfer_arranged` /
`voucher_issued` event carries its `entitlement` and the policy `reason`. The journey moves to
`RESOLVED` only if flight, seat and bag are done and every step succeeded. A failed step records
`needs_human` and leaves it `RESOLVING` (meal vouchers are still issued if only the hotel failed).

### Specialists (scenario 2a)

On a cancellation the Customer agent runs three specialists from `acme.customer.specialists`, each with
a typed request/response and `handle(request, emit)` (timeline entries via `emit`):
**IRROPS Rebooking** (earliest PSS flight whose arrival passes `connection_check`; rebooks the PNR),
**Seat** (same seat via DCS, else nearest window seat as a tier courtesy; the timeline says why) and
**Baggage** (`bag.reroute` via WorldTracer). If no flight is feasible, or no window seat is open, the
journey stays `RESOLVING` and a `needs_human` timeline entry is written.

### Proactive notification and acknowledgement (scenario 3a)

`CustomerRuntime.tick()` sends **one** consolidated WhatsApp message per journey, `notify_delay`
(default 6 min) after it first reaches `RESOLVED` (22:03 -> 22:09; quiet hours allow exactly this).
`LlmPort.compose_passenger_message` words it from Customer-owned journey facts; the text must contain
the cancelled and new flight, departure time, seat, hotel, transfer and voucher or a deterministic
template is sent instead (`notification_sent.source` = `llm` | `template`). Inbound messages go through
`handle_inbound`: `departure_after` -> open request, `REPLANNING`, hand-off to the `ChangeAgent`
interface (`RecordingChangeAgent` stub until #30); `acknowledge` with no open request ->
`resolution_acknowledged = True`; anything else -> `needs_human`, request stays open. The API
transcript is read from the whatsapp simulator (`message.send` now takes `attachments`).

## Timeline event contract

`GET /journeys/{id}/events` is typed in `app/backend/openapi.json` as a `TimelineEvent` union
discriminated by `type` (`acme.api.events`). Emitted events have strict payload schemas; events
that #14-#16 will emit (`sales_opportunity`, `offer_sent`, ...) are provisional. When you add or
change an event, update `acme.api.events`, regenerate the contract
(`uv run python -m acme.api.openapi`), and update `EVENT_TYPES` in
`app/frontend/src/console/types.ts`: the backend drift test and `contract.test.ts` fail otherwise.

## Sales agent and Customer A2A skills (scenario 4a)

The Sales agent is a separate ADK/A2A service: `ACME_SALES_TOKEN=<sales token> uv run acme-sales`
(port `ACME_SALES_PORT`, default 8801; it refuses to start without the token). It is structurally
unable to contact passengers: its only channels are the Customer A2A skills and the `inventory` MCP
server with the `sales` credential (`acme.sales.config.sales_mcp_config` lists only that server;
`acme-sim` answers 403 for every other server). `tests/sales` also asserts the package imports no
Customer, API, WhatsApp, payments, CRM, PSS or DCS code.

The Customer agent card (`/.well-known/agent-card.json`) lists three skills, served at `POST /a2a`
(JSON-RPC `message/send`, exactly one data part `{skill, args}`; text parts, extra keys and unknown
skills are rejected): `list_journeys`, `journey_status` (`{status, flight, resolution_acknowledged}`,
no PII) and `propose_offer` (structured `Offer` only, `extra="forbid"`; recorded as `proposed`,
guardrail evaluation is #15). When `journey_status` answers for an ineligible journey (not RESOLVED,
or not acknowledged) the Customer side records `sales_suppressed`.

The radar sends at most one proposal per (journey, flight) and prices with `margin_opt`. Sales is
PII-free so it prices at the best courtesy tier (Diamond); the guardrail re-checks against the real tier.

### Conversational replanning (scenario 3b)

`ReplanningChangeAgent` (`customer/specialists/change.py`) handles a `departure_after` request after the
#13 hand-off: earliest feasible flight departing after the asked time that still protects the onward
connection (AC1252 11:40 for "after 10am"), seat kept, bag re-routed, connection re-verified,
hotel->airport transfer rescheduled (`transfer_arranged`), request closed (`request_closed`:
`satisfied` | `unsatisfiable`) and the journey returned to `RESOLVED` with a confirmation message.
Nothing feasible keeps the booking and the passenger gets an honest explanation. A failed step records
`needs_human` and leaves the journey `REPLANNING`. Leaving `RESOLVED` invalidates live offers (#4).

### Guardrailed offer delivery (scenario 4b)

`propose_offer` (A2A, `CustomerSkills.propose_and_deliver`) runs `guardrail_offer.evaluate` at proposal
and again immediately before sending, both under a per-journey lock (`CustomerRuntime.journey_lock`),
so concurrent or duplicate proposals cannot both be approved or sent. Tier comes from the loyalty
server and inventory from the inventory server (customer credential). A proposal while the journey is
not RESOLVED is rejected with a `SkillError` and not recorded as an offer (#83); other rejections are
recorded as `rejected`. Sales gets back only `approved | rejected` plus reason codes. The passenger
message (boarding pass + hotel voucher summary + the optional offer, buttons Accept / No thanks) is
composed from Customer-owned data only: flight, cabin and seat come from the journey and inventory,
and only `price` (a bounded `Decimal`) comes from the offer; no Sales string is rendered or sent to the
LLM. If the journey leaves RESOLVED before sending, the offer is `invalidated` and nothing is sent.
Events: `guardrail_decision`, `offer_proposed`, `offer_sent`.

### One-tap conversion (scenario 5a)

`POST /whatsapp/{id}/actions` `accept_offer` runs `acme.customer.conversion.accept_offer` under the
same per-journey lock as offer delivery. It re-runs the full guardrail against the current journey and
current inventory (rule 5 excludes this offer by id) and needs the offer `sent`; otherwise nothing is
charged, the seat is unchanged, the offer is `invalidated` and the passenger gets an honest message.
Order: `seat.hold` -> `charge` (idempotency key = offer id) -> `seat.assign` + `boarding_pass.issue` ->
confirmation, offer `accepted`. A failure after the charge voids it (`charge.void`, event
`charge_voided`; if the void itself fails, `needs_human`). Duplicate taps do nothing more.
`decline_offer` ("No thanks") marks the offer `declined`, changes nothing else and sends nothing.

### Closed loop and journey memory (scenario 5b)

After the passenger's decision (accept, or "No thanks") `acme.customer.closure.close_loop` runs under the
journey lock. `customer/verify.py` checks that flight (PSS), seat (exactly one seat, the expected one),
hotel (concierge), baggage (WorldTracer routing) and upgrade/payment (one successful charge equal to the
offer price, or no net charge and no premium seat) agree with the journey. A mismatch records
`verification_failed` with per-check details and leaves the journey `RESOLVED`. On success it records
`verification_passed`, logs a CRM interaction, schedules the NPS survey (scheduler sim, 24h after the last
segment), records `analytics_recorded` (`offer_accepted` with the net margin, or `offer_declined`), moves
the journey to `CLOSED`, appends `memory_committed` and saves the journey to the `JourneyStore`
(`acme.memory`; SQLite, `ACME_JOURNEY_DB` for a file path, in-memory by default). A saved journey reloads
with an identical timeline. `GET /journeys/{id}` includes `verification`; the events stream over SSE.
