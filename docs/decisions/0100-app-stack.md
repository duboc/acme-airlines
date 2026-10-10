# ADR 0100 — Application stack for the IRROPS MVP

- **Status:** Accepted (2026-10-10)
- **Date:** 2026-10-09
- **Deciders:** acme-lead (proposer), product owner `admin@duboc.altostrat.com` (approver)
- **Context doc:** [`docs/product/irrops-use-case.md`](../product/irrops-use-case.md)
- **Plan:** [`docs/product/mvp-plan.md`](../product/mvp-plan.md)

## Context

We must build a multi-agent system that handles a flight cancellation for a
passenger before she notices it. The system:

- picks up an OCC cancellation event
- rebuilds the journey: flight, seat, bag, hotel, transfer and vouchers
- tells the passenger on WhatsApp, and replans when she asks for a change
- only then lets a Sales agent propose an upgrade, and only through the
  Customer agent ("Two Goals, One Guardrail")

Constraints that shape the choice:

1. **Google Cloud-native.** The factory runs in project `acme-airlines-factory`.
   Model calls use **Vertex AI on the `global` endpoint only**, with ADC. No API
   keys and no regional fallbacks.
2. **The enterprise systems are simulated** in the MVP: OCC, PSS, DCS, CRM/Loyalty,
   WorldTracer, the WhatsApp Business API, payments and hotel partners. Their
   interfaces should look the way real ones would, so a simulator can later be
   swapped for a real system.
3. **CI cannot reach Vertex AI.** GitHub Actions has no GCP credentials. Every
   automated test must be deterministic and run without a model, and live model
   tests must be opt-in.
4. **The workers are LLM agents** (Claude Sonnet for backend, Gemini Pro for
   frontend and QA). The stack should be mainstream and well documented, and PRs
   should stay small (under ~400 lines).
5. **Governance must be enforced by code and architecture, not by prompts.** A
   guardrail that lives only in an LLM prompt is not a guardrail.

## Decision

### Summary

| Concern | Choice |
|---------|--------|
| Backend language | **Python 3.12**, managed with **uv**. Lint/format with **ruff**, tests with **pytest** (+ `pytest-asyncio`). |
| Agent framework | **Google Agent Development Kit (ADK, Python)**. Workflow agents and custom `BaseAgent`s for the deterministic orchestration; `LlmAgent` only where language is needed. |
| Model | **Gemini on Vertex AI, `location=global`**, through `google-genai`/ADK with `GOOGLE_GENAI_USE_VERTEXAI=TRUE`, `GOOGLE_CLOUD_PROJECT=acme-airlines-factory`, `GOOGLE_CLOUD_LOCATION=global`, ADC. The model ID is configuration (default: a GA Gemini Flash model that the live smoke test confirms on `global`). Controlled by `LLM_MODE`: `fake` (the default for dev, tests and CI, $0) or `vertex` (live demo only, global endpoint, ADC). |
| Inter-agent protocol | **A2A**. The **Sales agent is a separate A2A service**, and its only outbound channel is an A2A client to three PII-free Customer agent skills: `list_journeys`, `journey_status` and `propose_offer` (structured `Offer` only). The Customer agent's specialists (IRROPS, Seat, Rights, Concierge, Change) are ADK sub-agents inside the Customer runtime in the MVP; each exposes a typed interface so it can be moved behind A2A later. |
| Enterprise integrations | **MCP tool servers** built with the official Python `mcp` SDK (FastMCP), over streamable HTTP. Every external system is mocked (`occ`, `pss`, `dcs`, `crm`, loyalty/FFP #32, `worldtracer`, `concierge`, `whatsapp`, `payments`, plus a PII-free `inventory` server for Sales), all mounted in one `acme-sim` process and sharing one seeded world state. `acme-sim` enforces **per-caller bearer tokens with a server allowlist** (Sales → `inventory` only). |
| Event stream | The OCC simulator exposes `flight_ops.subscribe` / `flight_ops.publish_cancellation` as MCP tools, with an in-process async event bus behind them. Pub/Sub comes after the MVP. |
| Policy code | Plain, pure Python modules with full unit tests: `margin_opt`, `guardrail_offer`, `duty_of_care` (ANAC Resolution 400 primary regime, rule table per #31), `connection_check` (minimum connection time). No LLM is involved in any policy decision. |
| Journey memory ("Memory Bank") | A `JourneyStore` port with an in-memory/SQLite adapter for the MVP. Vertex AI Agent Engine Memory Bank is a later adapter behind the same port. |
| Runtime API | **FastAPI**: REST for commands, plus **Server-Sent Events** for the live agent timeline. |
| Frontend | **TypeScript + React + Vite**. Lint with **ESLint**, unit tests with **Vitest** + Testing Library, end-to-end tests with **Playwright**. Two views: an **operator console** (timeline, journey state, guardrail decisions) and a **simulated WhatsApp passenger chat**. |
| Packaging / deploy target | One container image per service (`customer-runtime`, `sales-agent`, `acme-sim`, `frontend`). **docker compose** for local runs; **Cloud Run** is documented only in the MVP (#24; no deployment and no deploy identity; per-customer sandboxing comes after the MVP). |
| CI | **GitHub Actions** on every PR: ruff + pytest for the backend, ESLint + Vitest for the frontend, and Playwright end-to-end once the UI exists. Live-Vertex tests are marked `live` and skipped in CI. |

### Repository layout

```text
app/
  backend/                 # uv project "acme"
    pyproject.toml
    src/acme/
      domain/              # pydantic models, scenario fixture, clock
      policy/              # margin_opt, guardrail_offer, duty_of_care, connection_check
      sim/                 # simulated systems + MCP servers (acme-sim)
      llm/                 # Vertex/Gemini config (global only) + fake LLM for tests
      customer/            # Customer agent + specialist sub-agents (ADK)
      sales/               # Sales agent A2A service (ADK)
      memory/              # JourneyStore port + adapters
      api/                 # FastAPI app (REST + SSE)
    tests/
      unit/  scenario/  governance/  live/
  frontend/                # Vite + React + TS
.github/workflows/ci.yml
```

### How the guardrail is enforced

The guardrail sits in three layers. Each layer works on its own.

1. **Architecture.**
   - Sales holds a credential that `acme-sim` accepts **only** for the PII-free
     `inventory` server (cabin counts, fares). Calls to every other server are
     refused server-side (403). The check is deny-by-default and runs before
     routing, so it holds even for paths that aren't mounted. This is not just
     omitted from Sales' client config. The Customer credential may use every
     server, including `inventory` (read-only), which the guardrail needs.
   - Sales' only other channel is the Customer agent's A2A skills:
     - `list_journeys` returns open journey ids, with no PII.
     - `journey_status` returns `{status, flight, resolution_acknowledged}`, with no PII.
       When it answers for a journey that isn't eligible, the Customer side
       records `sales_suppressed`.
     - `propose_offer` accepts a **structured `Offer` only** (`extra="forbid"`,
       no free text). Every field is constrained (pattern, enum or bounded
       `Decimal`). The passenger message is always composed by the Customer
       agent from its **own** journey and inventory data, never from the
       offer's strings, so no Sales-authored string can reach the passenger or
       the Customer agent's LLM.
   - In deployment, Cloud Run service-to-service IAM restricts who may invoke
     the Customer A2A endpoint to the `sales-agent` identity. That backs the
     offer's `origin="sales"` claim with identity.
2. **State.**
   - The journey has a disruption state machine:
     `DETECTED → RESOLVING → RESOLVED ⇄ REPLANNING → CLOSED`.
   - It also has `open_requests` and `resolution_acknowledged`, which is set
     when the passenger confirms the current resolution and reset on every
     replanning.
   - Any transition out of `RESOLVED` invalidates every open offer.
   - The Sales radar is suppressed until the journey is `RESOLVED` **and**
     acknowledged.
3. **Policy code.**
   - `guardrail_offer.evaluate(journey, offer, inventory)` is a pure function.
     It approves an offer only if **all six rules** hold:
     1. the disruption is resolved (`RESOLVED`)
     2. the passenger has acknowledged the current resolution and has no open request
     3. it is a genuine discounted benefit for this passenger: below standard,
        at or below the tier's recovery price, the tier is eligible, and seats
        are open. The **standard fare comes from inventory, not from the
        offer**; a mismatching claim is rejected.
     4. the net margin is non-negative. The margin is **recomputed** as
        price − inventory cost floor; a mismatching claim is rejected.
     5. the offer is optional / zero-pressure, and **no other offer has already
        been made** for this journey (excluding the offer being evaluated): none
        in `approved`, `sent`, `accepted` or `declined`, and none `invalidated`
        on the same flight. In short: at most one live or answered offer per
        journey; an offer invalidated by replanning may be re-proposed only on
        a different flight. Evaluation and the status change run under a
        per-journey lock.
     6. it originates from Sales and is for the passenger's current flight
   - It is evaluated at proposal time **and re-evaluated in full at send time
     and at acceptance time** (time-of-check/time-of-use), against current
     inventory. At acceptance the seat is held before the charge, and any
     failure after the charge voids it. Every decision is written
     to the timeline with its reasons.

Governance tests attack each layer separately, including the time-of-check/time-of-use races:
`tests/governance/`, issues #22 and #29.

## Options considered

### Agent framework

| Option | Pros | Cons |
|--------|------|------|
| **ADK (chosen)** | Google-native, first-class Vertex/Gemini support, built-in A2A exposure (`to_a2a`, `RemoteA2aAgent`) and an MCP toolset, workflow agents for deterministic flows, deploys to Cloud Run or Agent Engine. | Younger API that changes fast, so we pin the version in `uv.lock`. Less community material than LangGraph. |
| LangGraph | Mature graph orchestration, large community. | Not Google-native, A2A/MCP are add-ons, and it adds a second paradigm next to the Google stack. |
| Hand-rolled orchestration on `google-genai` | Smallest dependency surface, full control. | We would rebuild A2A, MCP wiring, sessions and tracing ourselves, and it doesn't match the brief. |

### Process topology

| Option | Pros | Cons |
|--------|------|------|
| **Customer runtime with in-process specialists + separate Sales A2A service (chosen)** | A2A sits exactly where the governance boundary is, so the guardrail is architectural. There are few moving parts, tests are fast, and PRs stay small. | Specialists are not independently deployable yet. |
| Every agent is its own A2A service | Matches the brief diagram exactly. | About 7 services to run, wire and test before the first scenario works. CI is slower, and LLM workers would spend most of their effort on plumbing. |
| Single process, no A2A | Simplest. | Loses the A2A requirement and makes the Sales/Customer boundary a convention instead of a wall. |

### Simulated backends

| Option | Pros | Cons |
|--------|------|------|
| **MCP servers over one shared seeded world (chosen)** | Same protocol a real integration would use, so a simulator can be swapped for a real adapter. One process is easy to run. State stays consistent across systems, which scenario 5 verification needs. | MCP adds a little per-call overhead compared with direct function calls. |
| Plain Python fakes | Fastest. | Doesn't exercise MCP, and the brief names MCP explicitly. |
| WireMock / HTTP stubs | Language-neutral. | Stateless by default, so a consistent cross-system world is hard to get. |

### Frontend

| Option | Pros | Cons |
|--------|------|------|
| **React + Vite + TS (chosen)** | Mainstream, so the Gemini frontend worker knows it well. Good test tooling (Vitest/Playwright). Static build runs on Cloud Run or any CDN. | A second toolchain in CI. |
| FastAPI + HTMX/Jinja | One language, very small. | Weaker component testing, and a live SSE timeline is harder to build well. |
| ADK dev web UI only | Free. | It's a developer tool, not a product demo, and has no passenger chat simulation. |

### Memory Bank

| Option | Pros | Cons |
|--------|------|------|
| **Port + local adapter now, Agent Engine Memory Bank later (chosen)** | Deterministic tests, no GCP dependency in CI. | The real Memory Bank is not exercised in the MVP. |
| Vertex AI Agent Engine Memory Bank now | Real managed memory. | Needs GCP in tests and the right IAM, and it is a regional service, which conflicts with our global-only rule until we confirm it can be used without regional model calls. |
| Firestore | Serverless, cheap. | Not needed until we deploy more than one instance. |

## Consequences

- **Positive:**
  - All five scenarios can be demonstrated end-to-end on a laptop or the
    factory VM, with deterministic CI.
  - The guardrail is testable and enforced by code.
  - Each simulator can later be swapped for a real adapter behind the same MCP
    tool names.
  - **How ADK is wrapped (#64):** `acme.customer.adk_agent.CustomerAgent` is a
    deterministic custom `BaseAgent` (no `LlmAgent`, no model call) over the unchanged
    `CustomerRuntime`. `acme.sim.adk_connector.adk_connector` is a second `Connector`
    implementation built on ADK's `MCPToolset`; it needs a served `acme-sim` (ADK builds its
    own HTTP transport), while the plain in-process connector stays for tests and the demo.
    `google-adk[a2a]` is pinned in `uv.lock`. Vertex config loads only when `LLM_MODE=vertex`.
    The Customer A2A card (no skills yet) is served at `/.well-known/agent-card.json`; #14 adds
    the skills.
- **Negative / risks:**
  - ADK and A2A APIs move quickly. We mitigate by pinning versions and wrapping
    ADK usage in thin modules.
  - LLM-dependent behaviour (message wording, intent parsing) is covered by fakes
    in CI and only by an opt-in live smoke test, so wording regressions can slip
    through.
- **Follow-ups (after the MVP):**
  - move specialists behind A2A
  - Pub/Sub event stream
  - Agent Engine Memory Bank adapter, **only if it can be used without regional endpoints**; it needs its own ADR and PO approval
  - per-customer sandboxes on Cloud Run
  - real WhatsApp Business API

## Decisions

1. **Cloud Run:** document only in the MVP (#24). No deployment and no deploy identity.
2. **"Customer positive":** an explicit, deterministic passenger acknowledgement, not LLM sentiment.
3. **Fictional short block times** in the fixture (as pinned in #4), labelled as simulated.
4. **Gemini model:** a Flash model on the global endpoint by default; the model ID is configuration.
5. **`LLM_MODE`:** `fake` (the default for dev, tests and CI, $0) or `vertex` (live demo only, global endpoint, ADC).
6. **Every external system is mocked.** List them: OCC event stream, PSS, DCS, CRM, Loyalty/FFP (#32), WorldTracer, WhatsApp Business, Payments, Memory Bank (`JourneyStore`), hotel/transfer booking, boarding-pass/voucher issuing.
7. **Merge gate:** the latest review starting `VERDICT: APPROVE` + green CI; the lead merges autonomously.
8. **Duty-of-care source:** ANAC Resolution 400 is the primary regime (GRU departure); rule table per #31.
