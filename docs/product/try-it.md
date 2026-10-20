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

The demo features a **Guided Scenario Control Panel** embedded at the top of the Operations Console (`/console`) that lets you run scripted variants of Mariana Silva's AC1234 story with a single click—no typing of magic strings required.

---

## 2. Accessing the Demo

### Option A: Hosted Demo on Cloud Run (Recommended)

The demo is deployed as an IAP-protected Cloud Run service (`acme-irrops-demo`). In `ENGINE_MODE=agent-engine` (`.github/workflows/irrops-deploy.yml:25`), it acts as a stateless proxy that forwards `/scenarios*`, `/journeys*`, and `/whatsapp*` to `acme-customer-agent` and `acme-sales-agent` on Vertex AI Agent Engine running `LLM_MODE=vertex` (`app/backend/src/acme/engines/deploy.py:73`, `docs/product/engines.md:27`). The Cloud Run container itself sets `LLM_MODE=fake` (`app/demo/Dockerfile:27`, `infra/90-irrops-demo-platform.sh:60`) for the `$0` deterministic `ENGINE_MODE=local` fallback when Agent Engine resource names are absent.

#### Live Hosted URL
Click directly through to test the application live:
- **Operations Console:** **[https://acme-irrops-demo-vdhcml5ceq-uc.a.run.app/console](https://acme-irrops-demo-vdhcml5ceq-uc.a.run.app/console)**
- **WhatsApp Passenger Chat:** **[https://acme-irrops-demo-vdhcml5ceq-uc.a.run.app/whatsapp](https://acme-irrops-demo-vdhcml5ceq-uc.a.run.app/whatsapp)**
- **Service Base URL:** `https://acme-irrops-demo-vdhcml5ceq-uc.a.run.app`

For operators discovering or verifying the service URL from the command line:
```bash
gcloud run services describe acme-irrops-demo \
  --region us-central1 \
  --project acme-airlines-factory \
  --format='value(status.url)'
```

#### Operational Notes for Testers
- **Cold start:** If the Cloud Run service has scaled to zero due to inactivity, the initial request may take a few seconds while the container initializes. Subsequent requests respond instantly.
- **Shared session state:** The hosted demo runs on a single Cloud Run instance (`--max-instances=1`). In `ENGINE_MODE=agent-engine` (`irrops-deploy.yml:25`), `acme-irrops-demo` proxies `/scenarios*` and `/whatsapp*` to `acme-customer-agent` (`LLM_MODE=vertex` with cross-container replay tape), whereas in the `$0` `ENGINE_MODE=local` fallback (`app/demo/Dockerfile:27`, `infra/90-irrops-demo-platform.sh:60`) state is held in-memory with `LLM_MODE=fake`. All concurrent visitors share the active scenario session.
- **Resetting state:** If another tester or previous run left Mariana's journey in a modified, replanned, or closed state, you can reset the world at any time:
  - Click the **Reset** button in the header of the Operations Console (`/console`), or
  - Select any scenario in the **Guided scenario** control panel and click **Start scenario** to re-seed the simulation cleanly from scratch.

#### Who Can Sign In
Access is protected by Google Identity-Aware Proxy (IAP). Any Google account belonging to the authorized domains configured in `WEB_DOMAINS` (`infra/factory.local.env`) can sign in.

#### What to Expect
1. Open the hosted URL in your web browser.
2. Sign in with your authorized Google account via the Google sign-in prompt.
3. You will arrive directly at the **Operations Console** (`/console`) with the **Guided scenario** control panel loaded.

---

#### Which engine is serving?

The hosted demo runs either `local` (agents in the container) or `agent-engine` (agents on Vertex
AI Agent Engine). `GET /api/healthz` shows `"engine": "agent-engine"` in the second case. The
scenario presets and journeys behave the same in both modes; message wording may differ when the
live LLM is used.

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

When you open `/console`, the interface presents the operational controls and real-time state:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  Operations console                      [Trigger AC1234 cancellation]  [Reset]        │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  Guided scenario                                                                       │
│  [Demo, all systems mocked]                                                            │
│  Scenario [Happy path                         ▼]  [Start scenario]                     │
│  Scenarios 1-5 end to end: rebooked, replanned after 10am...                           │
│  Pass looks like: Journey CLOSED on AC1252, seat 2A, $135.00 charged once.             │
│                                                                                        │
│  [Next step]  Step 2 of 5. Next: Mariana: 'Can I leave after 10am?'                    │
│                                                                                        │
│  Expected now                                                                          │
│  Rebooked on AC1240 with seat 14A, hotel and vouchers. Sales holds back.               │
│  ✓ Matches the expectation                                                             │
│                                                                                        │
│  Manual passenger actions                                                              │
│  [Can I leave after 10am?]  [Perfect, thanks!]  [after 1pm]  [Accept]  [No thanks]     │
├─────────────────────────┬───────────────────────────────┬──────────────────────────────┤
│  Journey: MSLV42        │  Agent timeline (live SSE)    │  Passenger chat (simulated)  │
│  Mariana Silva          │  • OCC cancel AC1234 ingested │  ┌────────────────────────┐  │
│  [Detected]>[Resolving] │  • Rebooked on AC1240 (14A)   │  │ 10:09 PM               │  │
│  Flight: AC1240         │  • Hotel HT8842 booked        │  │ AC1234 cancelled...    │  │
│  Seat:   14A            │  • Meal vouchers issued       │  │ Rebooked AC1240 (14A)  │  │
│  Hotel:  HT8842         │  • Sales held back            │  │ Hotel HT8842 booked    │  │
│  Status: Resolved       │  • Guardrail: offer approved  │  └────────────────────────┘  │
└─────────────────────────┴───────────────────────────────┴──────────────────────────────┘
```

1. **Guided Scenario Panel (Top):**
   - **Scenario picker:** Dropdown to select from the 6 scripted scenario presets.
   - **Start scenario:** Resets the simulation world and seeds the selected preset variant.
   - **Description & Pass condition:** Shows the preset description and the explicit pass criteria (`Pass looks like: ...`).
   - **Next step:** Advances the simulation by one scripted action (OCC event, time progression, passenger reply, or button tap).
   - **Expected now box:** Live assertion card comparing the server state against preset expectations:
     - Shows `Matches the expectation` (green) or `Differs from the expectation` (red with specific discrepancies).
     - Displays `Last message to passenger` whenever a communication was sent.
   - **Manual passenger actions:** Quick-action buttons (`[Can I leave after 10am?]`, `[Perfect, thanks!]`, `[after 1pm]`, `[Accept]`, `[No thanks]`) to test manual inputs at any point.
2. **Journey Card (Bottom Left):**
   - Disruption status stepper: `Detected → Resolving → Resolved ⇄ Replanning → Closed`.
   - Live itinerary details: Flight number, seat, hotel accommodation, meal vouchers, and active upgrade offer.
   - Guardrail notice: Highlights when the passenger has not yet acknowledged the resolution.
3. **Agent Timeline (Bottom Center):**
   - Real-time Server-Sent Events (SSE) feed recording the actions taken by autonomous specialist agents.
   - Explicit guardrail audit entries: `Sales held back` and `Guardrail decision: approved / rejected`.
4. **Passenger Chat (Bottom Right):**
   - Real-time simulation of Mariana Silva's WhatsApp conversation.
   - Displays notifications, boarding passes, hotel vouchers, and interactive upgrade offer cards with `[Accept]` and `[No thanks]` buttons.

---

## 4. Walkthrough per Scenario Preset

### Preset 1: Happy path (`happy-path`)

The complete end-to-end narrative of Mariana Silva (scenarios 1–5).

- **Description:** `Scenarios 1-5 end to end: rebooked, replanned after 10am, acknowledged, upgrade accepted.`
- **Pass looks like:** `Journey CLOSED on AC1252, seat 2A, $135.00 charged once.`
- **Step-by-step:**
  1. Select **Happy path** from the Scenario dropdown and click **Start scenario**.
     - *State:* The world is reset.
  2. Click **Next step** (*Step 1: OCC cancels AC1234 (unscheduled maintenance)*).
     - *Expected now:* `Rebooked on AC1240 with seat 14A, hotel and vouchers. Sales holds back.`
     - *What happens:* Specialist agents automatically rebuild the journey on replacement flight AC1240 (departure 08:15, arrival 10:15 protects her 14:00 connection), assign seat 14A, book hotel HT8842, and issue meal vouchers.
     - *Console:* Journey status moves to **`Resolved`**. WhatsApp remains quiet (night hours respected). Timeline logs `Sales held back`.
  3. Click **Next step** (*Step 2: Time passes: Mariana gets the 22:09 resolution message*).
     - *Expected now:* `Rebooked on AC1240 with seat 14A, hotel and vouchers. Sales holds back.`
     - *What happens:* Exactly 6 minutes after detection, Mariana receives one consolidated WhatsApp resolution message containing her new flight, hotel booking, and vouchers.
     - *WhatsApp:* Message arrives. No sales pitch is included.
  4. Click **Next step** (*Step 3: Mariana: 'Can I leave after 10am?'*).
     - *Expected now:* `Replanned to AC1252 (after 10am), still seat 14A.`
     - *What happens:* Conversational ChangeAgent replans her to AC1252 departing at 11:40 (arrival 12:20 provides 100 min connection margin for AC0800 at 14:00, meeting 90 min MCT). Seat 14A is retained. Transfer booking is updated.
     - *Console:* Status updates to AC1252, seat 14A. Console displays: *"Passenger has not acknowledged the resolution yet."* Sales radar remains suppressed.
  5. Click **Next step** (*Step 4: Mariana: 'Perfect, thanks!'*).
     - *Expected now:* `Only now, after her acknowledgement: one optional upgrade offer is sent.`
     - *What happens:* Passenger acknowledges the resolution. Sales radar detects `resolution_acknowledged = True`, checks PII-free seat inventory (4 Premium Business seats open), calculates a 55% Diamond courtesy discount ($135 vs $290 standard fare, +$80 net margin), and submits a structured offer via A2A. Customer agent guardrail approves.
     - *WhatsApp:* An optional, zero-pressure card appears: *"Optional. You can ignore this. Upgrade to Premium Business on AC1252 for $135.00"*, with `[Accept]` and `[No thanks]` buttons.
  6. Click **Next step** (*Step 5: Mariana taps Accept on the upgrade*).
     - *Expected now:* `Journey CLOSED on AC1252, seat 2A, $135.00 charged once.`
     - *What happens:* Guardrail re-verifies offer validity. DCS holds seat 2A. Payment simulator charges $135.00. Seat 2A is confirmed and 14A is released. Boarding pass issued. Closed-loop verification passes across all systems.
     - *Console:* Status moves to **`Closed`**. Itinerary displays seat **2A (premium_business)**, charged **$135.00**. Evaluation shows: `Matches the expectation`.

---

### Preset 2: Declines the upgrade (`declines-upgrade`)

Tests the zero-pressure guarantee when a passenger declines the offer.

- **Description:** `Mariana answers 'No thanks' to the optional upgrade.`
- **Pass looks like:** `Keeps seat 14A on AC1252, nothing charged, and no further offer is ever made.`
- **Step-by-step:**
  1. Select **Declines the upgrade** and click **Start scenario**.
  2. Click **Next step** through steps 1–4 to reach the upgrade offer on AC1252.
  3. Click **Next step** (*Step 5: Mariana taps 'No thanks'*).
     - *Expected now:* `Offer declined: keeps 14A on AC1252, nothing charged, no further offer.`
     - *What happens:* Customer agent marks the offer `declined`. No payment charge is executed. Closed-loop verification confirms economy seat 14A and $0.00 charged. Journey closes cleanly.
     - *Console:* Status moves to **`Closed`**, seat remains **14A**, charged **$0.00**. No further offers are ever sent.

---

### Preset 3: Unsatisfiable request (`unsatisfiable-request`)

Tests boundary enforcement when a passenger makes a request that violates connection constraints.

- **Description:** `Mariana asks to leave after 1pm, which would miss her Santiago connection.`
- **Pass looks like:** `An honest message; she stays on AC1240; no offer.`
- **Step-by-step:**
  1. Select **Unsatisfiable request** and click **Start scenario**.
  2. Click **Next step** (*Step 1: OCC cancels AC1234*).
     - *Expected now:* `Rebooked on AC1240.`
  3. Click **Next step** (*Step 2: Time passes: Mariana gets the 22:09 resolution message*).
     - *Expected now:* `Resolution message sent.`
  4. Click **Next step** (*Step 3: Mariana: 'Can I leave after 1pm?'*).
     - *Expected now:* `An honest message: after 1pm is not possible. She stays on AC1240; no offer.`
     - *What happens:* ChangeAgent analyzes flight schedules and minimum connection times. Departing after 13:00 would arrive after 14:00, causing Mariana to miss her onward flight AC0800 in Santiago.
     - *WhatsApp:* Customer agent sends an honest reply explaining that an afternoon departure would miss her connection.
     - *Console:* Booking remains safely on **AC1240** (seat 14A). Because she has not acknowledged, no sales offer is ever sent.

---

### Preset 4: Seat 14A taken (`seat-taken`)

Tests courtesy seat allocation when the passenger's original seat is unavailable on the replacement aircraft.

- **Description:** `14A is no longer open on the replacement flight AC1240.`
- **Pass looks like:** `Nearest window seat 15A, with the reason recorded.`
- **Step-by-step:**
  1. Select **Seat 14A taken** and click **Start scenario**.
  2. Click **Next step** (*Step 1: OCC cancels AC1234 with 14A occupied*).
     - *Expected now:* `14A is taken: nearest window seat 15A on AC1240, with the reason recorded.`
     - *What happens:* World is seeded with 14A already taken on AC1240. Seat specialist identifies Mariana's window seat preference, allocates nearest open window seat **15A**, and records the courtesy rule.
     - *Console:* Itinerary displays seat **15A**. Timeline logs `Seat 15A assigned (courtesy window seat)`.
  3. Click **Next step** (*Step 2: Time passes: Mariana gets the 22:09 resolution message*).
     - *Expected now:* `Resolution message explains the seat change.`
     - *WhatsApp:* Resolution message explicitly notes that seat 15A was selected to preserve her window preference.

---

### Preset 5: Hotel unavailable (`hotel-unavailable`)

Tests graceful failure and human escalation when duty-of-care partner integrations fail.

- **Description:** `The partner hotel has no rooms.`
- **Pass looks like:** `needs_human, never RESOLVED, no offer and no charge.`
- **Step-by-step:**
  1. Select **Hotel unavailable** and click **Start scenario**.
  2. Click **Next step** (*Step 1: OCC cancels AC1234 with no partner hotel rooms*).
     - *Expected now:* `No hotel room: handed to a human. Never marked resolved, no offer.`
     - *What happens:* Duty of care mandates overnight accommodation under ANAC 400. Concierge simulator reports zero rooms available. The system flags `needs_human` and keeps disruption status in `Resolving`.
     - *Console:* Status remains **`Resolving`**. Notice indicates `Needs human: Yes`.
  3. Click **Next step** (*Step 2: Status check*).
     - *Expected now:* `Still with a human. No offer, no charge.`
     - *Console:* Sales radar remains completely locked out. Because the disruption is not resolved, **no sales offer is ever generated**.

---

### Preset 6: Upgrade race (`upgrade-race`)

Tests time-of-check / time-of-use (TOCTOU) race condition protection (verifying the issue #88 fix).

- **Description:** `Mariana asks to replan after 12pm, then taps the now-stale Accept button (#88).`
- **Pass looks like:** `The offer is invalidated, nothing is charged and she keeps seat 14A.`
- **Step-by-step:**
  1. Select **Upgrade race** and click **Start scenario**.
  2. Advance through steps 1–4 until the upgrade offer on AC1252 is displayed.
  3. Click **Next step** (*Step 5: Mariana: 'Can I leave after 12pm?'*).
     - *Expected now:* `Replanning invalidates the open offer.`
     - *What happens:* Transitioning back to `Replanning` automatically **invalidates** all active offers under `journey_lock`.
     - *Console:* Offer status changes to `invalidated`.
  4. Click **Next step** (*Step 6: Mariana taps the stale Accept button*).
     - *Expected now:* `Stale Accept: nothing charged, she keeps seat 14A.`
     - *What happens:* Acceptance handler checks offer status before taking payment. Detecting the invalidation, it rejects the action, charges $0.00, and retains seat 14A.
     - *Console:* Charged remains **`$0.00`**, seat **14A**. Evaluation confirms `Matches the expectation`.

---

## 5. Live Mode & Limitations

### Two-Mode Architecture (`ENGINE_MODE=agent-engine` vs `ENGINE_MODE=local`)
- **Hosted Agent Engine mode (`ENGINE_MODE=agent-engine`, `.github/workflows/irrops-deploy.yml:25`):** When `AGENT_ENGINE_CUSTOMER` and `AGENT_ENGINE_SALES` are configured, `acme-irrops-demo` runs `create_engine_app()` and proxies `/scenarios*`, `/journeys*`, and `/whatsapp*` to `acme-customer-agent` and `acme-sales-agent` on Vertex AI Agent Engine. `acme-customer-agent` runs with `LLM_MODE=vertex` (`app/backend/src/acme/engines/deploy.py:73`, `docs/product/engines.md:27`) on the Vertex AI `global` endpoint via Application Default Credentials (`gemini-2.5-flash`), so all 6 `/scenarios*` presets and `/whatsapp*` turns execute against the live Vertex model with deterministic replay tapes across container rebuilds (`docs/product/adr-0103-stateless-engine-scenarios.md`).
- **Local fallback (`ENGINE_MODE=local`, `LLM_MODE=fake`):** The Cloud Run `acme-irrops-demo` container sets `LLM_MODE=fake` (`app/demo/Dockerfile:27`, `infra/90-irrops-demo-platform.sh:60`) as a `$0` deterministic fallback when Agent Engine resource names are absent. In `ENGINE_MODE=local`, the preset scenarios API (`GET /scenarios`, `POST /scenarios/{id}/start`, `POST /scenarios/{id}/step`) is only mounted when `LLM_MODE=fake` (returning `404 Not Found` if `LLM_MODE=vertex` is set directly on the local in-process app).
- For deployment and architecture details, see [`docs/product/engines.md`](engines.md), [`docs/product/architecture.md`](architecture.md), and [`docs/product/deploy.md`](deploy.md).

### Known Limitations in MVP
1. **Single Narrative Fixture:** The simulator world is seeded with Mariana Silva's flight AC1234. Multi-passenger scaling is post-MVP.
2. **Single Cloud Run Instance:** The hosted demo runs with `--max-instances=1` because session state is held in-memory.
3. **English Only:** Passenger communication is in English. Multi-language localization is planned for future phases.

---

## 6. Factory Monitoring & Observatory

To monitor the operational health, cost, and agent activity of the software factory that built this application, visit the **ACME Observatory**:

- **Observatory URL:** [https://acme-observatory-vdhcml5ceq-uc.a.run.app](https://acme-observatory-vdhcml5ceq-uc.a.run.app) (IAP-protected)
- **Features:** Real-time factory spend, Gemini model token consumption, agent run traces, and budget guard status.
- **Details:** See the deployment docs.
