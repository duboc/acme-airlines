# Try-It Guide — ACME Airlines Autonomous IRROPS Demo

A quickstart and evaluation guide for the product owner and testers to explore the ACME Airlines autonomous IRROPS resolution and guardrailed upselling system.

---

## 1. Overview

This demo allows you to test how ACME Airlines handles flight cancellations and passenger recovery end-to-end, powered by autonomous multi-agent orchestration under the core governance principle:

> **"Two Goals, One Guardrail"** — *Service advocacy always outranks revenue.*
>
> 1. The airline proactively resolves the disruption before the passenger realizes it.
> 2. The Sales agent is structurally and policy-wise prevented from pitching an upgrade until the disruption is completely resolved and acknowledged by the passenger.
> 3. The Sales agent never contacts the passenger directly; all communication is composed and vetted by the Customer agent.

The demo features a **Guided Scenario Control Panel** that lets you run scripted variants of Mariana Silva's AC1234 story with a single click—no typing of magic strings required.

---

## 2. Accessing the Demo

### Option A: Hosted Demo on Cloud Run (Recommended)

The demo is deployed as an IAP-protected Cloud Run service (`acme-irrops-demo`) running in single-container mode with in-memory state (`LLM_MODE=fake`).

#### Hosted URL
Once the platform script has been executed by the operator (`infra/90-irrops-demo-platform.sh`), the service URL can be retrieved with:
```bash
gcloud run services describe acme-irrops-demo \
  --region us-central1 \
  --project acme-airlines-factory \
  --format='value(status.url)'
```

#### Who Can Sign In
Access is protected by Google Identity-Aware Proxy (IAP). Any Google account belonging to authorized domains can sign in:
- `duboc.altostrat.com` (including product owner `admin@duboc.altostrat.com`)
- `google.com`

#### What to Expect
1. Open the hosted URL in your web browser.
2. Sign in with your authorized Google account via the Google sign-in prompt.
3. You will arrive directly at the **Operator Console** (`/console`) with the **Guided Scenario** control panel loaded.

---

### Option B: Running Locally

You can run the demo locally on your machine using either the single-container demo image or Docker Compose.

#### Method 1: Single Demo Container (Fastest)
The single demo image packages the built frontend, FastAPI runtime, in-process simulators, and the Sales agent into one container:
```bash
docker build -f app/demo/Dockerfile -t acme-demo app
docker run --rm -p 127.0.0.1:8080:8080 acme-demo
```
Then open: **[http://localhost:8080/console](http://localhost:8080/console)**

#### Method 2: Docker Compose (4 Independent Services)
Runs each service (`customer-runtime`, `sales-agent`, `acme-sim`, `frontend`) in its own isolated container with network boundary enforcement:
```bash
export ACME_SIM_TOKENS="customer=$(openssl rand -hex 24),sales=$(openssl rand -hex 24),test-admin=$(openssl rand -hex 24)"
export ACME_SALES_TOKEN=$(echo "$ACME_SIM_TOKENS" | sed -n 's/.*sales=\([^,]*\).*/\1/p')
docker compose -f app/compose.yaml up --build
```
Then open: **[http://localhost:8080/console](http://localhost:8080/console)**

---

## 3. The Console Interface

When you open `/console`, the screen is divided into clear operational sections:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   ACME Airlines                                        │
│  [Console]  [WhatsApp]                                                Simulated data   │
├─────────────────────────┬───────────────────────────────┬──────────────────────────────┤
│  Guided scenario        │  Journey: MSLV42              │  Passenger WhatsApp          │
│  [Scenario Picker    ▼] │  [Detected]>[Resolving]>...   │  ┌────────────────────────┐  │
│  [Start scenario]       │                               │  │ 10:09 PM               │  │
│                         │  Itinerary                    │  │ AC1234 cancelled...    │  │
│  Expected now           │  Flight: AC1240               │  │ Rebooked AC1240        │  │
│  Matches expectation    │  Seat:   14A                  │  │ Seat 14A, Hotel HT8842 │  │
│                         │  Hotel:  HT8842               │  └────────────────────────┘  │
│  [Next step]            │                               │                              │
│  Step 2 of 5            │  Agent timeline (live SSE)    │  [Can I leave after 10am?]   │
│                         │  • Hotel HT8842 booked        │  [Perfect, thanks!]          │
│  Manual actions         │  • Meal vouchers issued       │  [Accept]  [No thanks]       │
│  [Replies & Taps]       │  • Sales held back            │                              │
└─────────────────────────┴───────────────────────────────┴──────────────────────────────┘
```

1. **Guided Scenario Panel (Left):**
   - **Scenario selector:** Choose from 6 pre-scripted scenario presets.
   - **Start scenario:** Resets the simulation world and seeds the selected preset.
   - **Next step:** Executes the next scripted action (OCC event, time progression, passenger reply, or button tap).
   - **Expected now:** Displays what the tester should observe, automatically evaluating current state against expectations ("Matches the expectation" or "Differs from the expectation").
2. **Journey Card & Itinerary (Center Top):**
   - Disruption status stepper: `Detected → Resolving → Resolved ⇄ Replanning → Closed`.
   - Current booking facts: Flight number, seat, hotel accommodation, duty-of-care meal vouchers, and active upgrade offer.
   - Guardrail notice: Alerts when the passenger has not yet acknowledged the resolution.
3. **Agent Timeline (Center Bottom):**
   - Live Server-Sent Events (SSE) feed showing behind-the-scenes actions of autonomous specialist agents (IRROPS Rebooking, Seat, Baggage, Rights & Compensation, Concierge, Sales Radar).
   - Explicit guardrail audit entries: `Sales held back` and `Guardrail decision: approved / rejected`.
4. **Passenger WhatsApp View (Right):**
   - Real-time simulation of Mariana Silva's WhatsApp conversation.
   - Shows messages, boarding passes, hotel vouchers, and interactive offer action buttons (`[Accept]` / `[No thanks]`).
   - Includes quick-action buttons for manual replies.

---

## 4. Walkthrough per Scenario Preset

### Preset 1: Happy Path (`happy-path`)

The complete end-to-end narrative of Mariana Silva (scenarios 1–5).

- **Objective:** Verify zero-touch rebuild, conversational replanning, acknowledged guardrail gating, and one-tap conversion.
- **Pass looks like:** `Journey CLOSED on AC1252, seat 2A, $135.00 charged once.`
- **Step-by-step:**
  1. Select **Happy path** and click **Start scenario**.
     - *State:* Status resets.
  2. Click **Next step** (*Step 1: OCC cancels AC1234*).
     - *What happens:* OCC maintenance cancellation ingested at 22:03 during quiet hours. Specialist agents rebuild journey to AC1240 (08:15 departure, arrival 10:15 protects 14:00 connection), hold seat 14A, book hotel HT8842, and issue meal vouchers.
     - *Screens:* Console shows status **`Resolved`**. WhatsApp remains silent (quiet hours respected). Timeline logs `Sales held back`.
  3. Click **Next step** (*Step 2: 22:09 resolution message*).
     - *What happens:* Exactly 6 minutes after detection, Mariana receives one consolidated resolution message on WhatsApp.
     - *Screens:* WhatsApp displays the rebooking message. Zero sales pitches are included.
  4. Click **Next step** (*Step 3: 'Can I leave after 10am?'*).
     - *What happens:* Conversational ChangeAgent replans to AC1252 at 11:40 (arrival 12:20 maintains 100 min connection margin for AC0800 at 14:00, meeting 90 min MCT). Seat 14A is retained. Transfer is rescheduled.
     - *Screens:* Console status updates to AC1252, seat 14A. Console shows notice: *"Passenger has not acknowledged the resolution yet."* Sales radar continues ticking but is suppressed.
  5. Click **Next step** (*Step 4: 'Perfect, thanks!'*).
     - *What happens:* Passenger acknowledges the resolution. Sales radar detects `resolution_acknowledged = True`, checks PII-free inventory (4 Premium Business seats open), calculates 55% Diamond courtesy discount ($135 vs $290 standard fare, +$80 net margin), and submits structured offer via A2A. Customer agent guardrail approves.
     - *Screens:* WhatsApp receives an optional, zero-pressure card: *"Optional. You can ignore this. Upgrade to Premium Business on AC1252 for $135.00"*. Buttons `[Accept]` and `[No thanks]` appear. Console timeline logs `Guardrail decision: approved`.
  6. Click **Next step** (*Step 5: Mariana taps Accept*).
     - *What happens:* Time-of-use guardrail re-check passes. DCS holds seat 2A. Payment simulator charges $135. Seat 2A is assigned and 14A is released. Boarding pass issued. Closed-loop verification confirms state consistency across PSS, DCS, hotel, baggage, and payments. Full timeline committed to JourneyStore.
     - *Screens:* Status moves to **`Closed`**. Itinerary shows seat **2A (premium_business)**. Timeline logs `Verification passed` and `Memory committed`.

---

### Preset 2: Declines Upgrade (`declines-upgrade`)

Tests the zero-pressure requirement when a passenger declines the offer.

- **Objective:** Verify that saying "No thanks" leaves the passenger on their chosen flight and seat with zero charge and no follow-up sales pressure.
- **Pass looks like:** `Offer declined: keeps 14A on AC1252, nothing charged, no further offer.`
- **Step-by-step:**
  1. Select **Declines the upgrade** and click **Start scenario**.
  2. Click **Next step** through steps 1–4 to reach the upgrade offer on AC1252.
  3. Click **Next step** (*Step 5: Mariana taps 'No thanks'*).
     - *What happens:* Customer agent marks offer `declined`. No payment charge is made. Closed-loop verification verifies economy seat 14A and $0.00 charge. Journey closes.
     - *Screens:* Console shows status **`Closed`**, seat **14A**, charged **$0.00**. No further offers are proposed.

---

### Preset 3: Unsatisfiable Request (`unsatisfiable-request`)

Tests boundary enforcement when a passenger makes a request that violates onward connection constraints.

- **Objective:** Verify that the agent refuses an impossible schedule, protects the passenger's connection, and gives an honest explanation.
- **Pass looks like:** `An honest message; she stays on AC1240; no offer.`
- **Step-by-step:**
  1. Select **Unsatisfiable request** and click **Start scenario**.
  2. Advance to Step 3 (*Mariana asks: 'Can I leave after 1pm?'*).
     - *What happens:* ChangeAgent checks flight schedules and minimum connection times. Departing after 13:00 would arrive after 14:00, missing her onward flight AC0800 in Santiago. The system refuses to strand her.
     - *Screens:* Customer agent sends an honest reply explaining that an afternoon departure would miss her Santiago connection. Her booking remains safely on **AC1240** (seat 14A). Because she has not acknowledged, no sales offer is ever sent.

---

### Preset 4: Seat 14A Taken (`seat-taken`)

Tests courtesy seat allocation when the passenger's original seat is unavailable on the replacement aircraft.

- **Objective:** Verify that when 14A is occupied, the seat specialist assigns the nearest window seat as a Diamond tier courtesy and explains the change transparently.
- **Pass looks like:** `14A is taken: nearest window seat 15A on AC1240, with the reason recorded.`
- **Step-by-step:**
  1. Select **Seat 14A taken** and click **Start scenario**.
  2. Click **Next step** (*Step 1: Cancellation with 14A occupied*).
     - *What happens:* Seeded world has 14A occupied on AC1240. Seat specialist identifies Mariana's window seat preference, allocates nearest open window seat **15A**, and logs the courtesy rule.
     - *Screens:* Console itinerary shows seat **15A**. Timeline logs `Seat 15A assigned (courtesy window seat)`.
  3. Click **Next step** (*Step 2: Resolution message*).
     - *Screens:* WhatsApp message arrives explicitly explaining that seat 15A was selected to preserve her window preference.

---

### Preset 5: Hotel Unavailable (`hotel-unavailable`)

Tests graceful failure and human escalation when duty-of-care partner integrations fail.

- **Objective:** Verify that if the hotel cannot be booked, the system flags `needs_human`, refuses to declare the disruption resolved, and completely locks out Sales.
- **Pass looks like:** `needs_human, never RESOLVED, no offer and no charge.`
- **Step-by-step:**
  1. Select **Hotel unavailable** and click **Start scenario**.
  2. Click **Next step** (*Step 1: Cancellation with no partner hotel rooms*).
     - *What happens:* Duty of care mandates overnight accommodation under ANAC 400. Concierge simulator reports no rooms available. The system logs `needs_human` and keeps disruption status in `Resolving`.
     - *Screens:* Console status remains **`Resolving`**. An indicator displays **`Needs human: Yes`**.
  3. Click **Next step** (*Step 2: Status check*).
     - *Screens:* Timeline confirms Sales is suppressed. Because the disruption is not resolved, **no sales offer is ever generated**.

---

### Preset 6: Upgrade Race Condition (`upgrade-race`)

Tests time-of-check / time-of-use (TOCTOU) race condition protection (verifying the issue #88 fix).

- **Objective:** Verify that if a passenger asks for another change while an offer is open, tapping the now-stale "Accept" button does not charge the passenger.
- **Pass looks like:** `The offer is invalidated, nothing is charged and she keeps seat 14A.`
- **Step-by-step:**
  1. Select **Upgrade race** and click **Start scenario**.
  2. Advance through steps 1–4 until the upgrade offer on AC1252 is displayed on WhatsApp.
  3. Click **Next step** (*Step 5: Mariana asks: 'Can I leave after 12pm?'*).
     - *What happens:* Transitioning to `Replanning` automatically **invalidates** all active offers under `journey_lock`.
     - *Screens:* Offer status in console changes to `invalidated`.
  4. Click **Next step** (*Step 6: Mariana taps stale Accept button*).
     - *What happens:* Acceptance handler re-evaluates the guardrail before charging. It recognizes the offer was invalidated by replanning, voids the action, charges $0.00, and leaves seat 14A intact.
     - *Screens:* Console verifies charged: **`$0.00`**, seat **14A**. WhatsApp receives a message explaining the offer is no longer valid.

---

## 5. Live Mode & Limitations

### Live Mode (`LLM_MODE=vertex`)
- The preset scenarios API (`GET /scenarios`) is a deterministic demo affordance that is **only active under `LLM_MODE=fake`**.
- Under `LLM_MODE=vertex`, the scenario endpoints return `404 Not Found`. Live Vertex mode uses real Gemini models on the Vertex AI `global` endpoint via Application Default Credentials (ADC) for free-form passenger chat and dynamic message composition.
- For instructions on running the live mode, see [`docs/product/demo-script.md`](demo-script.md) and [`docs/product/deploy.md`](deploy.md).

### Known Limitations in MVP
1. **Single Narrative Fixture:** The simulator world is seeded with Mariana Silva's flight AC1234. Multi-passenger scaling is post-MVP.
2. **Single Cloud Run Instance:** The hosted demo runs with `--max-instances=1` because session state is held in-memory.
3. **English Only:** Passenger communication is in English. Multi-language localization is planned for future phases.

---

## 6. Factory Monitoring & Observatory

To monitor the operational health, cost, and agent activity of the software factory that built this application, visit the **ACME Observatory**:

- **Observatory URL:** [https://acme-observatory-vdhcml5ceq-uc.a.run.app](https://acme-observatory-vdhcml5ceq-uc.a.run.app) (IAP-protected)
- **Features:** Real-time factory spend, Gemini model token consumption, agent run traces, and budget guard status.
- **Details:** See [`docs/observatory/platform-contract.md`](../observatory/platform-contract.md).
