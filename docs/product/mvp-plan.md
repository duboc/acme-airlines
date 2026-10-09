# MVP Plan — Proactive IRROPS Resolution & Guardrailed Upselling

- **Status:** Proposed. Implementation starts only after the product owner approves this PR.
- **Owner:** acme-lead · **Approver:** product owner (`admin@duboc.altostrat.com`)
- **Inputs:** [product brief](irrops-use-case.md) · [ADR 0100 — app stack](../decisions/0100-app-stack.md)

## 1. MVP goal

Show the whole Mariana Silva story (**scenarios 1–5**) running end to end:

- an operator console shows the agents rebuild the journey backstage
- a simulated WhatsApp phone shows what Mariana sees
- every step is governed by "Two Goals, One Guardrail" and proven by automated tests

The MVP must be runnable with one command and must pass CI with no cloud
credentials. The same build talks to real Gemini on Vertex AI (global endpoint)
when ADC is available.

## 2. Scope

### In scope: all five scenarios

All five scenarios are in scope because they form a single narrative: scenario 4
is only meaningful after 1–3, and scenario 5 verifies the earlier ones.

| # | Scenario | MVP behaviour | Notes |
|---|----------|---------------|-------|
| 1 | Silent ingestion & profile evaluation | OCC cancellation event → journey created, profile/PNR loaded, the SCL 14:00 connection constraint is derived, and no passenger contact happens during quiet hours. | Deterministic orchestration. |
| 2 | Zero-touch journey rebuild | AC1240 08:15, seat 14A held, bag re-routed, duty of care (HT8842, transfer, dinner voucher), journey `RESOLVED`. Sales suppressed throughout. | Duty of care uses a **simplified EU261-style rule table**, documented, not legal advice. |
| 3 | Proactive notification & replanning | One empathetic WhatsApp message with the full resolution. "After 10:00" → Change agent → AC1252 11:40, 14A kept, connection re-verified. | Gemini writes the wording and parses intent. Code validates every fact before sending, with a template fallback. |
| 4 | Negotiation & guardrailed upsell | Sales (separate A2A service) proposes a structured $135 / std $290 / +$80 offer on AC1252. The Customer agent's `guardrail_offer` approves it only when the journey is `RESOLVED` **and** Mariana has acknowledged the final resolution ("customer positive"). The check is repeated at send and accept time. | Governance-critical. |
| 5 | Conversion & closed-loop verification | One-tap accept → simulated payment → seat 2A → cross-system verification → journey stored → CRM log, NPS scheduled, analytics recorded → `CLOSED`. | Memory Bank is a local `JourneyStore` adapter. |

### Real vs simulated

| Component | MVP | Later |
|-----------|-----|-------|
| Gemini (Vertex AI, `global`, ADC) | **Real**: message composition, passenger intent parsing. A FakeLlm is used in CI. | Larger LLM role (e.g. sentiment, free-form Q&A). |
| ADK agents, A2A (Customer ↔ Sales), MCP protocol | **Real** | Specialists behind A2A as well. |
| OCC event stream | **Simulated** (MCP `flight_ops.subscribe` + in-process bus) | Pub/Sub / real OCC feed. |
| PSS, DCS, CRM/Loyalty, WorldTracer | **Simulated** MCP servers over one seeded world | Real adapters behind the same tool names. |
| WhatsApp Business API | **Simulated** (transcript + web chat UI) | Real WhatsApp Business API. |
| Payments, hotel/transfer partners | **Simulated** (idempotent) | Real PSPs / partner APIs. |
| Memory Bank | **Simulated** (`JourneyStore`: SQLite / in-memory) | Vertex AI Agent Engine Memory Bank, only if usable without regional endpoints (separate ADR + PO approval). |
| NPS survey, analytics | **Simulated** records | Real CRM / analytics. |
| Deployment | Containers + docker compose (real). Cloud Run is **documented**; it is deployed only if the PO approves (see ADR open question 1). | Cloud Run, one sandbox per customer. |

### Out of scope

- multiple passengers or flights at scale
- real PII
- authentication for the demo UI
- languages other than English
- legal-grade compensation calculation
- production SLOs

## 3. Architecture

```text
            ┌──────────────────────── frontend (React/Vite) ─────────────────────────┐
            │   Operator console (timeline, journey, guardrail)  │  WhatsApp sim     │
            └───────────────────────────────┬────────────────────────────────────────┘
                                            │ REST + SSE
                                   ┌────────▼─────────┐
                                   │  api (FastAPI)   │
                                   └────────┬─────────┘
                                            │ in-process
 ┌──────────────────────── customer-runtime (ADK) ─────────────────────────┐     A2A      ┌────────────────────┐
 │ Customer Agent (orchestrator, journey state machine, guardrail_offer)   │◄────────────►│ sales-agent (ADK)  │
 │  sub-agents: IRROPS · Seat · Baggage · Rights · Concierge · Change      │ propose_offer│ radar + margin_opt │
 │  policy: connection_check · duty_of_care · guardrail_offer              │              │ inventory MCP only │
 │  memory: JourneyStore            llm: Vertex Gemini (global) / FakeLlm  │              └─────────┬──────────┘
 └───────────────┬─────────────────────────────────────────────────────────┘                        │ MCP, sales token:
                 │ (customer token)                                                                 │ /mcp/inventory only
                 │ MCP (streamable HTTP)                                                            │
         ┌───────▼──────────────────────── acme-sim ───────────────────────────────────────────────▼──┐
         │ /mcp/occ  /mcp/pss  /mcp/dcs  /mcp/crm  /mcp/worldtracer  /mcp/concierge  /mcp/whatsapp     │
         │ /mcp/payments  /mcp/inventory (PII-free) — one seeded world; per-caller token allowlist      │
         └─────────────────────────────────────────────────────────────────────────────────────────────┘
```

Journey state machine: `DETECTED → RESOLVING → RESOLVED ⇄ REPLANNING → CLOSED`.

**Guardrail enforcement** (details in ADR 0100):

1. **Architecture.**
   - `acme-sim` refuses Sales' credential on every server except the PII-free `inventory` server.
   - Sales can only send a structured, fully constrained `Offer` (no free text) over A2A, and the Customer recomputes the fare and margin from inventory.
   - The passenger message is always written by the Customer agent.
2. **State.**
   - Offers are only possible when the journey is `RESOLVED` and the passenger has acknowledged the resolution.
   - Leaving `RESOLVED` invalidates every open offer.
3. **Policy.**
   - The pure `guardrail_offer` function decides, using six rules.
   - It is re-checked in full at proposal, send and accept time, with at most one live or answered offer per journey; an offer invalidated by replanning may be re-proposed only on a different flight (zero-pressure).

Each layer is tested on its own (#22, #29).

## 4. Milestones and backlog

Issue numbers refer to `duboc/acme-airlines`. ⟂ marks work that can run in
parallel. At most 4 workers run at once.

| Milestone | Issues | Exit criterion |
|-----------|--------|----------------|
| **M0 Foundations** | #3 CI + backend scaffold → ⟂ #4 domain/fixture, #9 LLM layer, #18 frontend scaffold | CI green on main with backend + frontend jobs, fixture merged with pinned times. |
| **M1 Policy, simulators, contract** | ⟂ #5 connection/duty-of-care, #6 margin/guardrail, #7 sim 1 (+inventory), #17 API contract, #21 QA harness; then ⟂ #8 sim 2a, #26 sim 2b, #27 sim access control | All simulators reachable over MCP with per-caller allowlist, policy at 100% branch coverage, scenario harness merged (strict xfail). |
| **M2 Scenarios 1–2** | #10 Customer agent + S1 (+ API wiring) → ⟂ #11 S2a, #12 S2b; ⟂ #19 console, #20 WhatsApp UI | Scenario 1–2 tests pass, and the console shows a live rebuild against real agents. |
| **M3 Scenario 3** | #13 notification + acknowledgement → #30 Change agent replanning | Scenario 3 tests pass; live Gemini smoke passes on the factory VM. |
| **M4 Scenarios 4–5** | #14 Sales A2A → ⟂ #22 governance part 1, #15 offer + delivery → #28 conversion → ⟂ #16 verification + memory, #29 governance part 2 | Scenario 1–5 tests all pass (no xfail left), both governance suites green. |
| **M5 Demo-ready** | ⟂ #23 Playwright E2E, #24 containers + deploy doc | Definition of done (§6) met. |

| # | Title | Role | Depends on |
|---|-------|------|------------|
| [#3](https://github.com/duboc/acme-airlines/issues/3) | CI: GitHub Actions lint + tests on PRs, backend scaffold | backend | — |
| [#4](https://github.com/duboc/acme-airlines/issues/4) | Domain model, state machine, structured `Offer`, Mariana fixture (pinned times) | backend | #3 |
| [#5](https://github.com/duboc/acme-airlines/issues/5) | Policy: connection_check + duty_of_care | backend | #4 |
| [#6](https://github.com/duboc/acme-airlines/issues/6) | Policy: margin_opt + guardrail_offer (6 rules) | backend | #4 |
| [#7](https://github.com/duboc/acme-airlines/issues/7) | Sim MCP 1: OCC, PSS, CRM, PII-free inventory | backend | #4 |
| [#8](https://github.com/duboc/acme-airlines/issues/8) | Sim MCP 2a: DCS, WorldTracer | backend | #7 |
| [#26](https://github.com/duboc/acme-airlines/issues/26) | Sim MCP 2b: Concierge, WhatsApp, Payments | backend | #7 |
| [#27](https://github.com/duboc/acme-airlines/issues/27) | Sim access control: deny-by-default per-caller tokens + allowlist | backend | #7, #8, #26 |
| [#9](https://github.com/duboc/acme-airlines/issues/9) | LLM layer: Vertex global + FakeLlm + no-keys/no-regions hygiene test | backend | #3 |
| [#10](https://github.com/duboc/acme-airlines/issues/10) | Customer agent + Scenario 1 + quiet hours + API wiring | backend | #5, #7, #17 |
| [#11](https://github.com/duboc/acme-airlines/issues/11) | Scenario 2a: rebooking, seat, baggage | backend | #8, #10 |
| [#12](https://github.com/duboc/acme-airlines/issues/12) | Scenario 2b: rights, concierge, RESOLVED | backend | #5, #10, #26 |
| [#13](https://github.com/duboc/acme-airlines/issues/13) | Scenario 3a: WhatsApp outreach + acknowledgement | backend | #9, #11, #12 |
| [#30](https://github.com/duboc/acme-airlines/issues/30) | Scenario 3b: Change agent replanning | backend | #13 |
| [#14](https://github.com/duboc/acme-airlines/issues/14) | Scenario 4a: Sales A2A service + suppression | backend | #6, #10, #27 |
| [#15](https://github.com/duboc/acme-airlines/issues/15) | Scenario 4b: guardrailed offer + delivery (send-time re-check) | backend | #14, #30 |
| [#28](https://github.com/duboc/acme-airlines/issues/28) | Scenario 5a: one-tap conversion (accept-time re-check) | backend | #8, #15, #26 |
| [#16](https://github.com/duboc/acme-airlines/issues/16) | Scenario 5b: verification + JourneyStore | backend | #28 |
| [#17](https://github.com/duboc/acme-airlines/issues/17) | Runtime API: REST + SSE + OpenAPI | backend | #4 |
| [#18](https://github.com/duboc/acme-airlines/issues/18) | Frontend scaffold + CI job | frontend | #3 |
| [#19](https://github.com/duboc/acme-airlines/issues/19) | Operator console | frontend | #17, #18 |
| [#20](https://github.com/duboc/acme-airlines/issues/20) | WhatsApp passenger chat | frontend | #17, #18 |
| [#21](https://github.com/duboc/acme-airlines/issues/21) | Test strategy + `ScenarioDriver` + scenario harness | qa | #4 |
| [#22](https://github.com/duboc/acme-airlines/issues/22) | Governance part 1: architecture, structured offers, policy, state | qa | #6, #14, #27 |
| [#29](https://github.com/duboc/acme-airlines/issues/29) | Governance part 2: outcome + time-of-check/time-of-use races | qa | #15, #28 |
| [#23](https://github.com/duboc/acme-airlines/issues/23) | Playwright E2E narrative | qa | #16, #19, #20 |
| [#24](https://github.com/duboc/acme-airlines/issues/24) | Containers, compose, Cloud Run doc (service-to-service IAM) | backend | #16, #18 |

The critical path is #3 → #4 → #7 → #10 → #11/#12 → #13 → #30 → #15 → #28 → #16 → #23.
Issues get `status:ready` when all their dependencies have merged. Generated
files (e.g. `openapi.json`, lockfiles) don't count toward the ~400-line PR limit.
The #4 and #10 authors should split their issue rather than run over the limit;
the lead then files the remainder as a follow-up issue.

## 5. Test strategy

The full strategy will be in `docs/product/test-strategy.md`, delivered by #21. Summary:

| Layer | What | Where | In CI |
|-------|------|-------|-------|
| Unit / policy | Domain model, state machine, `connection_check`, `duty_of_care`, `margin_opt`, `guardrail_offer` (100% branch coverage on policy) | `tests/unit` | ✅ |
| MCP contract | Every simulator tool via a real MCP client against the in-process server | `tests/unit/sim` | ✅ |
| Scenario | Scenarios 1–5 as Given/When/Then with fixed clock + FakeLlm, asserting every concrete fact in the brief. Written first as strict xfail. | `tests/scenario` | ✅ |
| Governance | Adversarial tests per guardrail layer (server-side isolation, structured offers, policy property tests) + outcome and time-of-check/time-of-use race tests | `tests/governance` (#22, #29) | ✅ |
| Frontend | Vitest component tests | `app/frontend/src/**` | ✅ |
| E2E | Playwright: full narrative through the UI | `app/frontend/e2e` | ✅ (PRs touching `app/`) |
| Live smoke | One real Gemini call on the `global` endpoint; optional live scenario 3 run | `tests/live` (`-m live`) | ❌ run on the factory VM, output pasted in PRs |

Rules:

- Tests are deterministic: no wall clock, no network, no model calls in CI.
- TDD: red → green → refactor.
- A scenario xfail is removed by the PR that implements it.

## 6. Definition of done (MVP)

The MVP is done when **all** of the following hold:

1. Issues #3–#24 and #26–#30 are closed by merged PRs. Each PR's latest `acme-reviewer` review starts with `VERDICT: APPROVE`, and each had green CI.
2. CI on `main` is green: backend lint/tests, frontend lint/tests/build, governance suite and Playwright E2E.
3. Scenario tests 1–5 pass with **no remaining xfail**. Every concrete fact in the brief is traced to a test (#21 table).
4. The governance suite proves:
   - `acme-sim` refuses Sales' credential on every server except `inventory`
   - Sales-authored text never reaches the passenger or the LLM
   - no offer is approved outside `RESOLVED` + acknowledged
   - no offer message reaches the passenger before she acknowledges the final resolution, and at most one live or answered offer per journey; an offer invalidated by replanning may be re-proposed only on a different flight
   - Sales' fare and margin claims are recomputed from inventory, and a paid upgrade never ends up without a seat
   - time-of-check/time-of-use races are safe
   - mutation checks show the suites catch a broken guardrail
5. `docker compose -f app/compose.yaml up` brings up the demo, and an operator can run the full narrative from the console + WhatsApp view.
6. The live smoke against Vertex AI `global` passes from the factory VM. There are no API keys and no regional endpoints anywhere in the code (checked by the reviewer, plus the hygiene test from #9).
7. The docs are current:
   - `app/README.md` (run/test)
   - simulator catalogue
   - policy rules
   - test strategy
   - deploy doc
   - this plan's status updated
8. The product owner has seen the demo and accepted it.

## 7. Delivery process

- One issue → one branch (`feat/<n>-<slug>`) → one PR (`Closes #n`, verification evidence), under ~400 changed lines.
- The lead dispatches at most 4 workers at once, preferring parallel issues in different areas. Wave 1 after #3: #4, #9, #18.
- Every PR gets an `acme-reviewer` comment review starting with `VERDICT: …`. The lead merges (squash) only when the latest verdict is `VERDICT: APPROVE` **and** CI is green.
- The lead sends the PO a status after each merge and at least every 2 hours of activity.

## 8. Risks

| Risk | Mitigation |
|------|------------|
| ADK / A2A / MCP SDK API churn | Pin versions in `uv.lock`, keep thin wrapper modules, keep contract tests on MCP tools. |
| LLM wording omits a resolution fact | Code-side fact validation before sending, with a template fallback (#13). |
| Gemini model ID not available on `global` | #9 confirms the model ID live, and the model is configuration. |
| Scope creep from a single narrative | Fixture-driven world. Generalising to many passengers comes after the MVP. |
| Cross-issue integration friction (API wiring) | Contract-first API (#17). #10 wires the real runtime, and each scenario issue keeps the API/SSE in sync. A shared `ScenarioDriver` protocol (#21) and explicit dependencies in issues. |
| Shared GitHub identity: the reviewer cannot formally approve PRs (same account as author), and the token cannot read checks/statuses | **Decided by the PO (2026-10-09):** the reviewer posts a comment review whose first line is exactly `VERDICT: APPROVE` / `VERDICT: REQUEST CHANGES`. The lead merges only when the latest verdict is APPROVE and CI is green, verified through the Actions runs API (`gh run list --branch <b>`). There is no branch protection, so nobody pushes to `main`. |
| CI cannot exercise Vertex | FakeLlm in CI, live smoke on the VM with output pasted in PRs. |

## 9. Decisions requested from the product owner

1. Approve the stack (ADR 0100) and this scope (all five scenarios, simulated enterprise systems).
2. **Cloud Run:** deploy in the MVP (needs a deploy identity) or document only? The default is document only.
3. **"Customer positive":** implemented as an explicit passenger acknowledgement, not LLM sentiment (ADR open question 2).
4. **Simulated block times:** the brief's times can't hold with real GRU→SCL flight durations. The fixture uses fictional short block times (ADR open question 3).
5. **Model:** Gemini Flash (default) or Pro for passenger messages?
6. ~~Merge gate under a shared GitHub identity~~: **decided by the PO.** The merge gate is the latest `VERDICT: APPROVE` + green CI (see Risks).
