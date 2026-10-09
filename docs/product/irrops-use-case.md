# Product Brief — Proactive IRROPS Resolution & Guardrailed Ancillary Upselling

> Source: product owner (duboc), 2026-10-09. This is the first goal for the
> acme-airlines software factory. The factory's lead agent turns it into an
> MVP plan, a stack proposal and a GitHub Issues backlog.

## Use case overview

**Primary use case:** proactive, autonomous airline flight-disruption (IRROPS)
resolution and guardrailed ancillary upselling.

**Business objective:** transform flight cancellations from reactive customer
friction into a seamless, automated recovery experience that protects passenger
rights, eliminates call-center strain, and creates contextual revenue
opportunities.

## Key actors & systems

- **Primary customer:** Mariana Silva — ACME Frequent Flyer **Diamond** tier, with
  an onward connection in Santiago.
- **ACME Customer Agent (orchestrator):** dedicated runtime VM powered by Gemini.
  Sole goal: passenger advocacy, duty-of-care fulfilment and seamless journey
  restoration.
- **Sales Manager (Revenue Agent):** yield management, cabin monetisation, margin
  optimisation. Strict communication guardrail — **cannot contact passengers directly**.
- **Specialised sub-agents (A2A network):**
  - **IRROPS Rebooking Agent** — schedules, interline options, minimum connection times.
  - **Seat Agent** — preserves seat attributes (e.g. window 14A, tier courtesy).
  - **Rights & Compensation Agent** — regulatory duty-of-care (EU261 / local frameworks).
  - **Concierge & Ancillaries Agent** — hotels, ground transfers, digital meal vouchers.
  - **Change Agent** — mid-journey replanning and itinerary amendments.
- **Enterprise systems:** Operations Control Center (OCC), Passenger Service System
  (PSS), Departure Control System (DCS), CRM, Loyalty/FFP, WorldTracer (baggage),
  WhatsApp Business API.

## Operational scenarios

### Scenario 1 — Silent ingestion & autonomous profile evaluation
- **Trigger:** 10:03 PM, OCC publishes a cancellation for **AC1234 (GRU → SCL)**
  (unscheduled maintenance).
- **Perception:** the Customer Agent listens on an MCP event stream
  (`flight-ops.subscribe()`); outside business hours it activates without alarming
  the passenger.
- **Context:** loads Mariana's CRM profile, Diamond tier, active PNR (seat 14A,
  1 checked bag) and detects the high-stakes constraint: onward international
  connection at **2:00 PM tomorrow in Santiago**.

### Scenario 2 — Zero-touch backstage journey rebuilding
- **Rebooking:** Customer Agent ↔ IRROPS Agent (A2A) finds **AC1240 (08:15 AM
  nonstop)**, preserving the 2:00 PM connection.
- **Seat & luggage continuity:** Seat Agent holds **14A** on the new aircraft; an
  MCP call updates WorldTracer to retag and route the bag to AC1240.
- **Duty of care:** Rights & Compensation Agent runs a compliance script confirming
  overnight accommodation, meals and ground transport; Concierge Agent books partner
  hotel **#HT8842**, arranges transfers, issues a digital dinner voucher.
- **Sales containment:** Sales Agent watches Premium Business inventory but is
  suppressed by governance — **no pitches during active disruption resolution**.

### Scenario 3 — Proactive notification & conversational replanning
- **10:09 PM:** empathetic WhatsApp message with the cancellation **and** the
  completed resolution (flight, seat, hotel, transfer, vouchers).
- **New constraint:** Mariana asks for a departure **after 10:00 AM**.
- **Replanning:** Change Agent finds **AC1252 (11:40 AM)**, re-verifies the Santiago
  connection, keeps 14A, updates the itinerary in seconds.

### Scenario 4 — Inter-agent negotiation & guardrailed upsell
- **Opportunity:** with the issue resolved and the customer positive, Sales Agent
  flags 4 open Premium Business seats on AC1252.
- **Pricing:** `margin_opt.py` computes a recovery discount of **$135** (standard
  $290), **+$80 net margin**.
- **Guardrail:** Sales Agent pitches to the Customer Agent over A2A; Customer Agent
  runs `guardrail_offer.py`: (1) core disruption 100 % resolved? (2) genuine
  discounted benefit for a Diamond customer after a disruption? → approved as an
  optional, zero-pressure courtesy.

### Scenario 5 — Frictionless conversion & closed-loop verification
- Boarding passes and hotel vouchers via WhatsApp, plus the optional $135 lie-flat offer.
- One-tap acceptance → payments API → seat **2A** Premium Business confirmed.
- Verification scripts check flight, seat, hotel, baggage and upgrade are in sync
  across PSS/DCS; journey state committed to the persistent **Memory Bank**; CRM log,
  post-trip NPS survey scheduled, conversion analytics recorded.

## Legacy vs. agentic

| Dimension | Legacy operations | ACME multi-agent model |
|-----------|-------------------|------------------------|
| Operational posture | Reactive (waits for the passenger) | Proactive (detects and resolves backstage) |
| Context | Fragmented across web, app, desk, phone | Single persistent memory across channels |
| Disruption handling | Passenger queues / chases rebooking | Rebooking, hotel, vouchers pre-issued |
| Passenger rights | Only on explicit demand | Calculated and fulfilled by policy code |
| Ancillary revenue | Generic blasts at bad times | High-propensity, guardrailed upsell during recovery |
| Passenger effort | High | Near zero (one-tap on WhatsApp) |

## Architecture & governance blueprint

```text
[Operational Event: OCC Cancellation]
                 │
                 ▼
     [ACME Customer Agent] ── Dedicated VM runtime (Google Cloud / Gemini)
                 │
  ┌──────────────┼──────────────┬──────────────┬──────────────┐
  ▼              ▼              ▼              ▼              ▼
[IRROPS]      [Seat]       [Baggage]      [Rights]       [Concierge]
Rebooking     14A Hold     WorldTracer    Duty-of-Care   Hotel/Voucher
  └──────────────┴──────────────┴──────────────┴──────────────┘
                                │
                 [Proactive WhatsApp Outreach]
                                │
     [Customer Dynamic Preference: Departure > 10:00 AM]
                                │
     [Itinerary Adjusted: AC1252 @ 11:40 AM + Connection Kept]
                                │
                 [Sales Agent: Margin Radar]
                                │ (A2A pitch: $135 upgrade)
                                ▼
               [Customer Agent Guardrail Check]
                                │ (Approved: genuine courtesy)
                                ▼
            [WhatsApp Delivery + 1-Tap Passenger Acceptance]
                                │
         [Seat 2A Lie-Flat Confirmed ── +$80 Net Margin]
```

- **Runtime isolation:** one dedicated sandbox per customer (Cloud Run / Compute
  Engine) to contain blast radius.
- **Protocols:** inter-agent orchestration via **A2A**; legacy integrations via
  **MCP** and function-calling tools.
- **Core governance rule — "Two Goals, One Guardrail":** service advocacy always
  supersedes revenue. Sales agents propose commercial actions **only through**
  customer agents.
