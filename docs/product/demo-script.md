# Demo Script — Proactive IRROPS Resolution & Guardrailed Upsell

Step-by-step presentation script for demonstrating the ACME Airlines autonomous multi-agent system (scenarios 1–5).

---

## 1. Overview & Narrative Arc

This demo showcases a multi-agent system resolving an airline flight cancellation before the passenger realizes the disruption has occurred, guided by the governing principle: **"Two Goals, One Guardrail"** — *service advocacy always outranks revenue*.

### The Persona & Itinerary
- **Passenger:** Mariana Silva (`PAX-7701`, Diamond loyalty tier, 250,000 miles).
- **Original Itinerary:** Flight **AC1234** departing São Paulo (GRU) at 22:45, arriving Santiago (SCL) next morning, connecting to **AC0800** at 14:00 (high-stakes business meeting).
- **Booking details:** Economy class, Seat **14A**, 1 checked bag (`BAG-AC-99211`).
- **Disruption:** At 22:03, OCC detects an unscheduled technical maintenance issue and cancels flight AC1234.

### The Five Scenarios
1. **Scenario 1:** Silent Ingestion & Profile Evaluation (10:03 PM / 22:03)
2. **Scenario 2:** Zero-Touch Journey Rebuild (Backstage)
3. **Scenario 3:** Proactive Notification & Conversational Replanning (10:09 PM / 22:09)
4. **Scenario 4:** Negotiation & Guardrailed Upsell (Sales A2A)
5. **Scenario 5:** Conversion & Closed-Loop Verification

---

## 2. Environment Setup

### Presenter Layout
Arrange two browser windows side by side:
- **Left Window (Operator Console):** `http://localhost:8080/console`
- **Right Window (Passenger WhatsApp):** `http://localhost:8080/whatsapp`

---

### Option A: Offline Demo (`LLM_MODE=fake`) — Recommended Default
Zero cost ($0), offline, deterministic fakes, no cloud credentials required.

1. Generate random per-caller sim tokens and start Docker Compose:
   ```bash
   export ACME_SIM_TOKENS="customer=$(openssl rand -hex 24),sales=$(openssl rand -hex 24),test-admin=$(openssl rand -hex 24)"
   export ACME_SALES_TOKEN=$(echo "$ACME_SIM_TOKENS" | sed -n 's/.*sales=\([^,]*\).*/\1/p')
   docker compose -f app/compose.yaml up --build
   ```

2. Wait for `customer-runtime` healthcheck to pass, then open the browser windows:
   - Console: `http://localhost:8080/console`
   - WhatsApp: `http://localhost:8080/whatsapp`

---

### Option B: Live Vertex AI Demo (`LLM_MODE=vertex`)
Uses live Gemini models on Google Cloud Vertex AI using Application Default Credentials (ADC).

1. Verify environment:
   - Project: `acme-airlines-factory`
   - Location: `global` (global endpoint only; regional endpoints and API keys are strictly forbidden by architectural rules).
   - ADC available (`gcloud auth application-default login` or VM service account).

2. Launch Docker Compose with Vertex enabled:
   ```bash
   export ACME_SIM_TOKENS="customer=$(openssl rand -hex 24),sales=$(openssl rand -hex 24),test-admin=$(openssl rand -hex 24)"
   export ACME_SALES_TOKEN=$(echo "$ACME_SIM_TOKENS" | sed -n 's/.*sales=\([^,]*\).*/\1/p')
   LLM_MODE=vertex docker compose -f app/compose.yaml up --build
   ```

---

## 3. Step-by-Step Presentation Walkthrough

### Scenario 1: Silent Ingestion & Profile Evaluation

**Context for Audience:**
> *"At 10:03 PM, ACME Flight 1234 from São Paulo to Santiago is cancelled due to aircraft maintenance. Rather than waking the passenger in panic during quiet hours, the system ingests the event and evaluates the constraints backstage."*

**Presenter Action:**
- In the **Operator Console** window, click the button: **`Trigger AC1234 cancellation`**.

**Backstage System Behavior:**
- OCC cancellation event is received.
- Customer orchestrator agent evaluates passenger profile:
  - Mariana Silva (Diamond tier, seat 14A, 1 checked bag).
  - High-stakes constraint identified: must preserve onward Santiago connection AC0800 at 14:00.
- Quiet-hours policy (22:00–07:00) is evaluated: at 22:03 local time, **proactive contact is held back** until a complete resolution is prepared.

**Expected Screen States:**
- **Operator Console:**
  - Disruption status advances from `Detected` to `Resolving`.
  - Timeline displays: `Cancellation detected: AC1234`, `Passenger profile loaded: Mariana Silva (Diamond)`, `Quiet hours evaluated`.
- **Passenger WhatsApp:**
  - **Completely silent.** No message appears.

---

### Scenario 2: Zero-Touch Journey Rebuild

**Context for Audience:**
> *"While Mariana is asleep, specialized autonomous agents collaborate through typed interfaces to rebuild her flight, re-protect her connection, book her hotel, arrange transfers, and issue duty-of-care meal vouchers under ANAC Resolution 400."*

**Backstage System Behavior:**
- **IRROPS Rebooking Specialist:** Scans PSS schedules and evaluates minimum connection times with `connection_check`. Selects AC1240 departing 08:15 (arriving 10:15, providing 135 minutes margin before the 14:00 connection).
- **Seat Specialist:** Consults DCS and holds seat **14A** on the new aircraft.
- **Baggage Specialist:** Re-routes bag `BAG-AC-99211` onto AC1240 via WorldTracer.
- **Rights & Compensation Specialist:** Evaluates **ANAC Resolution 400** (Brazil departures): overnight wait (~10.2h) triggers DOC-03 (dinner voucher), DOC-04 (breakfast voucher), and DOC-05 (hotel accommodation + airport transfers).
- **Concierge Specialist:** Books hotel **HT8842**, books round-trip airport transfers, and issues meal vouchers.
- **State Transition:** Disruption moves to **`RESOLVED`**.
- **Sales Radar:** Ticks every second over A2A, but is **suppressed**: no offer is allowed while the passenger has not acknowledged the resolution.

**Expected Screen States:**
- **Operator Console:**
  - Disruption status badge: **`Resolved`**.
  - Itinerary card:
    - Flight: `AC1240` (08:15 GRU → 10:15 SCL)
    - Seat: `14A (economy)`
    - Hotel: `HT8842`
    - Vouchers: `meal`
  - Timeline shows: `Rebooked on AC1240`, `Seat 14A held`, `Hotel HT8842 booked`, `Meal vouchers issued`, `Sales suppressed`.
- **Passenger WhatsApp:**
  - Still quiet during the 6-minute consolidation delay.

---

### Scenario 3: Proactive Notification & Conversational Replanning

#### Part 3A: Proactive Notification (10:09 PM / 22:09)
**Context for Audience:**
> *"At 10:09 PM — exactly 6 minutes after detection — the system delivers one consolidated, empathetic WhatsApp message with the completed resolution. Notice that no upsell pitch is attached."*

**Presenter Action / System State:**
- The notification timer fires (or in automated tests: clock advances 6 minutes).
- The consolidated WhatsApp message arrives in the passenger chat.

**Expected Screen State (WhatsApp):**
- One message from ACME Airlines detailing:
  - Flight AC1234 cancellation.
  - Rebooking on Flight AC1240 (departing 08:15).
  - Seat 14A retained.
  - Hotel HT8842 booked with airport transfers.
  - Dinner and breakfast vouchers attached.
- Zero upsell buttons or revenue pitches.

---

#### Part 3B: Conversational Replanning
**Context for Audience:**
> *"Mariana reads the message and realizes 08:15 is too early. She asks for a later flight. Watch how the conversational ChangeAgent updates her journey in seconds without losing her seat or onward connection."*

**Presenter Action:**
- In the **Passenger WhatsApp** window, in the message box, type:
  ```text
  Can I leave after 10am?
  ```
- Click **`Send`**.

**Backstage System Behavior:**
- Inbound message parsed (`departure_after 10:00`).
- Disruption status transitions from `RESOLVED` to **`REPLANNING`**.
- `ReplanningChangeAgent` identifies the earliest feasible flight: **AC1252** departing at **11:40** (arrives 12:20, leaving 100 minutes to connect to AC0800 at 14:00, comfortably exceeding the 90-minute MCT).
- Seat 14A held on AC1252; hotel transfer rescheduled; vouchers preserved.
- Disruption status returns to **`RESOLVED`**, but `resolution_acknowledged` is reset to `False`.
- Sales radar continues ticking, but remains **strictly suppressed** because the passenger has not acknowledged this new resolution.

**Expected Screen States:**
- **Passenger WhatsApp:**
  - Customer agent replies immediately: confirms rebooking to **AC1252** departing at **11:40**, seat **14A** preserved, transfer rescheduled.
- **Operator Console:**
  - Flight: **`AC1252`**, Seat: **`14A`**.
  - Notice visible: *"Passenger has not acknowledged the resolution yet."*
  - Disruption status: **`Resolved`**.

---

### Scenario 4: Negotiation & Guardrailed Upsell

**Context for Audience:**
> *"Now Mariana confirms the change. Only after she is positive and the resolution is acknowledged does the Sales agent receive permission to propose an offer. And even then, the Sales agent cannot speak to her directly — it must submit a structured proposal to the Customer agent's guardrail."*

**Presenter Action:**
- In the **Passenger WhatsApp** window, type:
  ```text
  Perfect, thanks!
  ```
- Click **`Send`**.

**Backstage System Behavior:**
- Customer agent parses acknowledgment (`resolution_acknowledged = True`).
- Sales agent radar queries A2A skill `journey_status`: eligibility check passes!
- Sales agent inspects inventory (PII-free MCP): 4 open seats in Premium Business on AC1252.
- Sales agent calculates recovery pricing via `margin_opt`:
  - Standard upgrade fare: $290.00
  - Diamond courtesy discount: 55% off → $135.00
  - Airline cost floor: $55.00 → Net margin: +$80.00
- Sales agent sends structured `Offer` to Customer agent skill `propose_offer`.
- Customer agent evaluates `guardrail_offer.evaluate()`: all 6 policy rules pass.
- Customer agent composes and renders the message using its own wording and inventory data (no Sales string reaches the passenger).

**Expected Screen States:**
- **Passenger WhatsApp:**
  - An optional, zero-pressure card appears:
    - Text: *"Optional. You can ignore this."*
    - Content: *"Upgrade to Premium Business on AC1252 for $135.00 (standard fare $290.00)."*
    - Two clear action buttons: **`[Accept]`** and **`[No thanks]`**.
- **Operator Console:**
  - Timeline records: `Guardrail decision: approved` and `Offer sent`.
  - Upgrade offer row: `$135.00`.

---

### Scenario 5: One-Tap Conversion & Closed-Loop Verification

**Context for Audience:**
> *"Mariana taps 'Accept'. In one motion, the system executes time-of-use guardrail verification, holds seat 2A, processes the payment, releases her old seat, issues her new boarding pass, verifies state consistency across all simulated enterprise systems, and commits her journey to memory."*

**Presenter Action:**
- In the **Passenger WhatsApp** window, click the **`Accept`** button.
  *(Note for presenter: Clicking 'No thanks' would cleanly decline the offer, preserve seat 14A, and close the journey without charge).*

**Backstage System Behavior:**
- **Time-of-Use Guardrail:** Re-evaluates rule 5 and inventory availability before charging.
- **DCS Seat Hold:** Holds seat **2A** in Premium Business.
- **Payments:** Charges $135.00 with idempotency key (`offer_id`).
- **Seat Assignment & Release:** Assigns seat **2A** to Mariana; releases seat **14A** back to inventory.
- **Boarding Pass:** Issues boarding pass for AC1252, seat 2A.
- **Closed-Loop Verification (`customer/verify.py`):** Cross-checks PSS flight, DCS seat (2A), Concierge hotel, WorldTracer bag routing, and Payments ($135.00). All match.
- **Closure:**
  - Memory Bank (`JourneyStore`): Commits full journey timeline.
  - CRM interaction logged; NPS survey scheduled (+24h).
  - Analytics recorded: `offer_accepted` (net margin +$80.00).
  - Disruption status transitions to **`CLOSED`**.

**Expected Screen States:**
- **Passenger WhatsApp:**
  - Outbound confirmation: *"You're all set in Seat 2A (Premium Business) on AC1252."*
  - Boarding pass with seat 2A displayed.
- **Operator Console:**
  - Disruption status badge: **`Closed`**.
  - Itinerary: Seat **`2A (premium_business)`**.
  - Timeline entries:
    - `Seat held: 2A`
    - `Charge succeeded ($135.00)`
    - `Seat assigned: 2A`
    - `Boarding pass issued: 2A`
    - `Verification passed: flight, seat, hotel, baggage, upgrade in sync`
    - `Memory committed to JourneyStore`
    - `Journey closed`

---

## 4. Key Architectural Takeaways for Q&A

1. **How the Guardrail Is Enforced (3 Layers):**
   - **Layer 1 (Architecture):** Sales holds credentials only for `/mcp/inventory`; all other systems return 403. Sales' only connection to Customer is A2A with structured, constrained `Offer` payloads.
   - **Layer 2 (State Machine):** The journey state machine enforces that Sales is suppressed unless status is `RESOLVED` and `resolution_acknowledged = True`.
   - **Layer 3 (Policy Code):** Pure Python function `guardrail_offer.evaluate()` checks 6 rules at proposal time, send time, and accept time under a per-journey lock.
2. **Deterministic CI & Global Vertex:**
   - 100% deterministic test suite (690+ tests) runs offline with `LLM_MODE=fake` in CI.
   - Live mode uses Vertex AI on the `global` endpoint with ADC (project `acme-airlines-factory`). No regional endpoints or API keys.
