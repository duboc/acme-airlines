# Milestone 4 Security & Guardrail Audit Report

- **Date:** 2026-10-10
- **Auditor:** `acme-security-auditor`
- **Target Commit:** `76fd345` (`main` branch)
- **Scope:** Complete audit of `app/backend` covering all governance layers of "Two Goals, One Guardrail", simulator isolation, A2A interfaces, prompt injection vectors, secret handling, payment safety, and Vertex AI compliance.
- **Reference Issue:** [#34](https://github.com/duboc/acme-airlines/issues/34)

---

## 1. Executive Summary

| Category | Status | Details |
|---|---|---|
| **Layer 1: Sim Access Control** | **PASS** | Deny-by-default allowlist, per-caller bearer tokens, path traversal resistant |
| **Layer 2: A2A Surface & Sales Isolation** | **PASS** | Structured PII-free skills, strict `Offer` schema, Sales agent isolated from passengers |
| **Layer 3: Policy & Atomic Delivery** | **PASS** | Deterministic 6-rule guardrail, send/accept TOCTOU re-checks, per-journey lock |
| **PII Protection** | **PASS** | Passenger names, PNRs, seats, and contact info never exposed to Sales |
| **Prompt Injection Defense** | **PASS** | Strict typing on parser output, deterministic templates, no tools exposed to LLM |
| **Secrets & Credentials** | **PASS** | No API keys, no credentials committed, random in-memory tokens if unset, sanitized logs |
| **Vertex AI Compliance** | **PASS** | ADC only, `acme-airlines-factory` project, `global` location strictly enforced |
| **Payment Safety** | **PASS** | Idempotency keys, hold-before-charge, post-charge TOCTOU re-check, automatic voiding |

### Findings Count
- **Audit Findings (New Vulnerabilities):** 0
  - Critical (P0): 0
  - High (P1): 0
  - Medium (P2): 0
  - Low / Info: 0
- **Pre-Audit Historical Defects Resolved During M4:** 4
  - Discovered by automated test suites: 3 ([#83](https://github.com/duboc/acme-airlines/issues/83) via #22 governance suite, [#88](https://github.com/duboc/acme-airlines/issues/88) via #29 race suite, [#97](https://github.com/duboc/acme-airlines/issues/97) via #23 Playwright E2E)
  - Discovered by security review: 1 ([#87](https://github.com/duboc/acme-airlines/issues/87) via PR #86 review)

---

## 2. Layers Audited & Bypass Verification

### Layer 1: Simulator Access Control & Isolation ([#27](https://github.com/duboc/acme-airlines/issues/27))
- **Architecture:** `acme-sim` mounts all external enterprise simulators behind `AccessControlMiddleware` (`acme/sim/auth.py`). The allowlist is evaluated **before routing**, ensuring unmounted or unexpected routes fail closed (`403 Forbidden`).
- **Caller Isolation:**
  - `sales`: Permitted to access `inventory` only (`ALLOWLIST["sales"] = frozenset({"inventory"})`).
  - `customer`: Full access across all mounted simulators.
  - `test-admin`: Test-only credential for test runners.
- **Path Traversal & Trick Resistance:**
  - Trailing slashes: `/mcp/inventory` and `/mcp/inventory/` correctly route to inventory; non-allowed endpoints like `/mcp/pss` and `/mcp/pss/` are rejected with `403 Forbidden`.
  - Encoded and double slashes: Enforces `if "//" in path or "%" in path: return False`. Percent-encoded sequences (`%2e%2e`, `%2f`, `%20`) are rejected before routing.
  - Case sensitivity: `_SERVER_PATH` regex enforces lowercase `^/mcp/([a-z0-9_-]+)...`. Casing anomalies (`/MCP/`, `/mcp/PSS`) fail regex and return `403 Forbidden`.
  - Subpath containment: Subpaths under `/mcp/inventory/*` stay bounded within the `inventory` FastMCP ASGI mount, preventing mount escaping.
- **Authentication:**
  - Parses `Authorization: Bearer <token>`.
  - Compares tokens using `hmac.compare_digest` in a constant-time loop over all registered callers without early termination.
  - Missing, empty, malformed, or non-Bearer headers return `401 Unauthorized`.
  - When unset, `load_tokens()` generates cryptographically random 32-byte tokens per process that are never printed to logs or stdout.

### Layer 2: Structured A2A Boundary & Sales Agent Containment ([#14](https://github.com/duboc/acme-airlines/issues/14))
- **A2A Protocol Surface (`POST /a2a`):**
  - Minimal JSON-RPC `message/send` handler (`acme/customer/skills.py`).
  - Rejects text parts and multi-part messages: strictly requires `len(parts) == 1 and isinstance(parts[0], DataPart)`.
  - Requires exact data envelope keys: `set(data) == {"skill", "args"}`.
  - Only three skills exposed: `list_journeys`, `journey_status`, and `propose_offer`. All other skills return JSON-RPC `-32602`.
- **Structured Offer Schema (`acme/domain/models.py`):**
  - `Offer` model enforces `extra="forbid"` and `frozen=True`.
  - Every string field is strictly constrained by regex or literals:
    - `journey_id`: `^J-[0-9a-f]{8}$`
    - `flight`: `^[A-Z]{2}\d{3,4}$`
    - `cabin`: `Literal["premium_business"]`
    - `seat_hint`: `^\d{1,2}[A-K]$`
    - `currency`: `Literal["USD"]`
    - `origin`: `Literal["sales"]`
    - `optional`: `Literal[True]`
  - Smuggling free text, promotional pitches, or arbitrary metadata is impossible.
- **Sales Agent Package (`acme/sales/`):**
  - Static AST analysis confirms `sales/` imports zero customer runtime, simulator internals, or database modules.
  - Contains no references to passenger messaging channels (WhatsApp) or payment dispatchers.
  - Agent card exposes `skills: []` with no inbound execution vectors.

### Layer 3: Policy Guardrail, Time-of-Use Re-checks & Atomic Locking ([#6](https://github.com/duboc/acme-airlines/issues/6), [#15](https://github.com/duboc/acme-airlines/issues/15), [#28](https://github.com/duboc/acme-airlines/issues/28), [#88](https://github.com/duboc/acme-airlines/issues/88))
- **Deterministic 6-Rule Policy (`acme/policy/guardrail_offer.py`):**
  1. `disruption_resolved`: Journey must be `RESOLVED`.
  2. `resolution_acknowledged`: Passenger must acknowledge resolution; no open requests pending.
  3. `genuine_discount`: Flight and cabin must match inventory, open seats > 0, price below standard fare and within tier cap.
  4. `margin_verified`: Price minus cost floor recomputed and matches claimed margin (non-negative).
  5. `zero_pressure`: Must be optional; `_BLOCKING_OTHER_STATUSES = {"approved", "sent", "accepted", "declined"}` prevents duplicate offers; re-proposing on the same flight after invalidation is blocked.
  6. `origin_and_flight_verified`: Origin is "sales"; flight matches journey's current flight.
- **Atomic Per-Journey Locking:**
  - `CustomerRuntime.journey_lock(journey_id)` ensures strict mutual exclusion across:
    - Proposal and delivery (`propose_and_deliver`)
    - Inbound passenger messages and replanning (`handle_inbound`)
    - Conversion and acceptance (`accept_offer`)
    - Offer decline (`decline_offer`)
- **Send-Time Time-of-Use Re-check:**
  - Re-evaluates all 6 rules immediately prior to dispatching WhatsApp notification. If state changed mid-composition, offer transitions to `invalidated` and no message is sent.
- **Accept-Time Time-of-Use Re-check:**
  - Re-evaluates all rules before `dcs:seat.hold`.
  - Re-evaluates all rules again **after `payments:charge` and before `dcs:seat.assign`**. If state moved to `REPLANNING`, the charge is immediately voided (`charge_voided`, reason `journey_changed`), and seat assignment is aborted.

---

## 3. Specific Security Dimensions

### 3.1 PII Protection & Data Minimization
- **Audit:** Examined `list_journeys` and `journey_status` responses and all Sales-facing schemas.
- **Verification:**
  - `list_journeys()` outputs only anonymous journey identifiers (`["J-xxxxxxxx"]`).
  - `journey_status()` exposes strictly `{status, flight, resolution_acknowledged}`. Names, PNRs, contact details, seat assignments, and loyalty tiers are completely excluded.
  - Sales prices offers assuming courtesy tier `diamond` without knowing the passenger's tier; the Customer guardrail authoritatively checks the offer against the tier loaded directly from the `loyalty` MCP server.
  - Zero PII leaks to Sales.

### 3.2 Prompt Injection Defense & LLM Port Integrity
- **Audit:** Examined `VertexLlm` (`acme/llm/vertex.py`), message composition, and inbound classification.
- **Verification:**
  - **Passenger Message Composition:** Input context is built exclusively from Customer-owned facts (CRM name, DCS seat, PNR flight, standard fare). Only `price` is sourced from the offer (as a validated, bounded `Decimal`). Sales `seat_hint`, `origin`, and IDs are completely excluded from the LLM prompt and output. A post-generation regex check guarantees required facts exist, falling back to a deterministic template if validation fails.
  - **Inbound Message Parsing:** Inbound text is classified into a closed discriminated union: `DepartureAfter(time)`, `Acknowledge()`, or `Other(text)`. No tools are exposed to the classification model. Unrecognized or injected commands fall into `Other`, triggering a `needs_human` operator escalation and halting upsell.

### 3.3 Secrets & Credentials Handling
- **Audit:** Examined credential management across backend and simulators.
- **Verification:**
  - No API keys or tokens are hardcoded.
  - `test_no_keys_no_regions.py` scans all files in `app/` and enforces that `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `api_key=`, and regional endpoints do not exist.
  - Simulator tokens in `ACME_SIM_TOKENS` are validated and never logged. Unset tokens generate secure random urlsafe strings with startup notices printing `(not shown)`.

### 3.4 Google Cloud & Vertex AI Architecture Compliance
- **Audit:** Examined `acme/llm/config.py` and environment initialization.
- **Verification:**
  - `PROJECT = "acme-airlines-factory"` and `LOCATION = "global"` strictly enforced. Setting regional locations or API keys raises `LlmConfigError` and refuses to start.
  - `load_llm_mode()` defaults to `"fake"` ($0 dev/CI operation).
  - Vertex AI operates strictly via Google ADC (Application Default Credentials).

### 3.5 Financial Safety & Payment Idempotency
- **Audit:** Examined `PartnerState.charge` (`acme/sim/partners.py`) and `conversion.py`.
- **Verification:**
  - **Execution Ordering:** `seat.hold` strictly precedes `payments.charge`. A hold failure aborts the transaction before billing.
  - **Idempotency:** Payment calls require `idempotency_key=str(offer.offer_id)`. Replayed requests return the existing receipt without duplicate charges. Duplicate or concurrent user taps return `accepted` without extra charges.
  - **Compensating Transactions (Void):** Any failure during or after charging triggers `payments:charge.void`. If voiding fails, an urgent `needs_human` event is dispatched for operator intervention.
  - Net charged amount in all error pathways is guaranteed to be $0.00.

---

## 4. Historical Vulnerabilities Resolved During M4

1. **[Issue #83](https://github.com/duboc/acme-airlines/issues/83) — `propose_offer` accepted proposals outside `RESOLVED`**
   - *Discovery Source:* **Automated tests** (Phase 1 governance test suite [#22](https://github.com/duboc/acme-airlines/issues/22)).
   - *Impact:* Proposals submitted during `RESOLVING` or `REPLANNING` were recorded into journey state.
   - *Fix:* Resolved in PR #85. `propose_offer` now evaluates the guardrail and raises `SkillError("rejected: disruption_not_resolved")` if not `RESOLVED`, recording nothing.
2. **[Issue #88](https://github.com/duboc/acme-airlines/issues/88) — `accept_offer` TOCTOU race between check and charge**
   - *Discovery Source:* **Automated tests** (Phase 2 governance race suite [#29](https://github.com/duboc/acme-airlines/issues/29) / `test_races.py`).
   - *Impact:* Inbound passenger change requests did not acquire the journey lock, allowing a transition to `REPLANNING` to occur between the pre-charge check and `payments:charge`.
   - *Fix:* Resolved in PR #93. `handle_inbound` now acquires `rt.journey_lock(rec.state.journey_id)`, and `_convert` runs a post-charge guardrail re-check that immediately voids the payment if state shifted.
3. **[Issue #87](https://github.com/duboc/acme-airlines/issues/87) — Economy hold remained active in DCS upon upgrade**
   - *Discovery Source:* **Security audit** (PR #86 pre-merge security review by `acme-security-auditor`).
   - *Impact:* Upgrading to 2A left the original 14A hold in DCS.
   - *Fix:* Resolved in PR #89. Added `seat.release` tool and automatic release upon seat assignment.
4. **[Issue #97](https://github.com/duboc/acme-airlines/issues/97) — Duplicate proactive notification under concurrent `tick()`**
   - *Discovery Source:* **Automated tests** (CI Playwright E2E narrative suite [#23](https://github.com/duboc/acme-airlines/issues/23) / PR #94).
   - *Impact:* Concurrent `tick()` executions (e.g. overlapping operator console and passenger chat polls) raced between the `notification_due()` check and `notify_resolution()`, delivering duplicate WhatsApp resolution notifications.
   - *Fix:* Resolved in PR #99. Enforces atomic check-and-send with double-checked locking under `rt.journey_lock(journey_id)` within `CustomerRuntime.tick()`, guaranteeing exactly one consolidated WhatsApp notification.

---

## 5. Audit Methodology & Verification Evidence

- **Static Analysis & Code Inspection:** Audited all source files in `app/backend/src/acme/` (`sim/auth.py`, `customer/skills.py`, `customer/conversion.py`, `customer/runtime.py`, `policy/guardrail_offer.py`, `llm/vertex.py`).
- **AST Architecture Verification:** Verified forbidden package import boundaries in `test_radar.py` and `test_architecture.py`.
- **Automated Regression Test Execution:**
  - `tests/sim/test_access_control.py` (sim isolation, path traversal, token comparison, token leakage)
  - `tests/governance/test_architecture.py` (three-tier structure, AST boundaries)
  - `tests/governance/test_structured_offers.py` (offer constraints, schema enforcement)
  - `tests/governance/test_policy_properties.py` (guardrail rule table coverage)
  - `tests/governance/test_state.py` (state machine transitions and invalidations)
  - `tests/governance/test_races.py` (TOCTOU interleavings, concurrent taps, racing proposals)
  - `tests/customer/test_offer_delivery.py` (delivery pipeline, prompt injection defense)
  - `tests/customer/test_conversion.py` (payment safety, hold ordering, voiding, idempotency)
  - `tests/unit/test_no_keys_no_regions.py` (global-only Vertex, zero keys scan)

---

## 6. Conclusion & Verdict

The IRROPS MVP implementation in `app/backend` demonstrates complete adherence to the "Two Goals, One Guardrail" governance model. Service advocacy strictly supersedes revenue, Sales is physically isolated from passenger communications and PII, payments are fail-closed and idempotent, and prompt injection pathways are safely neutralized.

**AUDIT VERDICT: PASS**
