# Exploratory Acceptance Test Report: ACME Airlines IRROPS MVP (v0.1.0)

**Date:** 2026-10-10  
**Tester:** `acme-qa-tester` (Standing QA Integration & Acceptance Agent)  
**Deliverable Tested:** ACME Airlines Disruption Resolution & Guardrailed Upsell MVP  
**Branch:** `test/35-acceptance-mvp`  
**Related Issue:** Closes #35  
**Verdict:** **PASS-WITH-NITS** (All 5 core golden-path product scenarios and business guardrails pass with evidence; three non-blocking resiliency/UX defects filed for post-MVP hardening)

---

## 1. Executive Summary

This acceptance test report validates the running end-to-end application described in `docs/product/irrops-use-case.md` and `docs/product/mvp-plan.md`.

Testing was executed against the running application using simulated MCP enterprise backends (`acme-sim`), the Customer Agent API runtime (`acme.api`), the Sales Agent A2A margin radar (`acme.sales`), and the React/Vite operator console and WhatsApp client (`app/frontend`). The testing exercised:
1. **Mariana Silva's complete AC1234 narrative** (Scenarios 1 through 5).
2. **Six mandatory edge cases and non-functional requirements**:
   - Infeasible replanning request (connection conflict handling)
   - Offer decline ("No thanks")
   - Rapid double-tap on offer acceptance (idempotency)
   - Mid-flow browser refresh recovery
   - Narrow mobile viewport (375 × 667 px)
   - Keyboard-only navigation and accessibility

All core brief facts passed verification end-to-end. Three non-blocking defect issues were identified, documented with Given/When/Then reproductions, and filed in GitHub Issues (`#104`, `#105`, `#106`).

---

## 2. Concrete Brief Fact Acceptance Matrix

| Scenario | Brief Requirement / Fact | Observed Behavior | Status | Evidence / Screenshot |
| :--- | :--- | :--- | :---: | :--- |
| **1** | OCC cancellation for AC1234 (GRU → SCL) due to unscheduled maintenance | Triggered via console button; `cancellation_detected` event logged with flight `AC1234` and reason `unscheduled maintenance`. | **PASS** | `01-console-initial.png`<br>`02-backstage-resolved.png` |
| **1** | Silent backstage ingestion without alarming passenger during quiet hours (22:00–07:00) | Journey transitioned backstage `UNRESOLVED` → `RESOLVING` → `RESOLVED` at 22:03 without sending any premature WhatsApp message. | **PASS** | `02-backstage-resolved.png` |
| **1** | Profile evaluation loads Diamond tier, PNR `MSLV42`, seat `14A`, 1 checked bag, and onward SCL connection at 2:00 PM | Loaded CRM profile (`diamond`), PNR `MSLV42` (seat 14A, 1 bag), and noted onward flight `AC0800` departing SCL at 14:00. | **PASS** | `02-backstage-resolved.png` (Timeline: `Profile loaded`) |
| **2** | Rebooking finds AC1240 (08:15 AM nonstop), preserving 2:00 PM connection | Earliest feasible nonstop flight AC1240 booked; connection margin calculated at 225 min. | **PASS** | `02-backstage-resolved.png` (Itinerary: `AC1240`) |
| **2** | Seat continuity holds 14A on AC1240 | Seat Agent assigned preferred window seat `14A` on AC1240 via DCS. | **PASS** | `02-backstage-resolved.png` (Seat: `14A (economy)`) |
| **2** | Luggage continuity routes checked bag `BAG0001` to AC1240 | WorldTracer MCP tool retagged and routed bag `BAG0001` to AC1240. | **PASS** | `02-backstage-resolved.png` (Bag routing: `BAG0001`) |
| **2** | Duty of care: Rights & Compensation assesses entitlements | Confirmed overnight accommodation, airport ground transfers, and dinner voucher. | **PASS** | `02-backstage-resolved.png` (Timeline: `Duty of care assessed`) |
| **2** | Concierge books partner hotel #HT8842, transfers, and meal vouchers | Concierge booked partner hotel HT8842 (booking ref `HB-AD0362EF`), arranged airport transfers, and issued digital dinner voucher. | **PASS** | `02-backstage-resolved.png` (Vouchers: `meal`, Hotel booked) |
| **2** | Sales containment: Sales Agent radar suppressed during active disruption | Sales radar checked journey status; Customer Agent reported unacknowledged disruption and logged `sales_suppressed`. | **PASS** | `02-backstage-resolved.png` (Timeline: `Sales held back`) |
| **3** | Proactive consolidated WhatsApp message sent at 10:09 PM (6-min delay) | At 22:09, passenger received exactly 1 message mentioning `AC1234`, `AC1240`, `14A`, and `HT8842`. | **PASS** | `03-whatsapp-first-message.png` |
| **3** | Passenger requests departure after 10:00 AM | Inbound message *"Can I leave after 10am?"* parsed by LLM port to `DepartureAfter(10:00)`. | **PASS** | `04-replanning-ac1252.png` |
| **3** | Change Agent replans to AC1252 (11:40 AM), keeping 14A, bag routed, transfer updated | Rebooked to AC1252 (11:40), kept seat 14A, luggage rerouted, hotel-to-airport transfer rescheduled, connection verified. | **PASS** | `04-replanning-ac1252.png` (Itinerary: `AC1252`) |
| **4** | Sales Agent radar flags 4 open Premium Business seats on AC1252 | Radar polled inventory for AC1252; identified 4 open lie-flat seats (`2A`, `2B`, `3A`, `3B`). | **PASS** | `05-guardrailed-offer-bubble.png` |
| **4** | Dynamic recovery pricing: $135 discount (standard $290, +$80 net margin) | Computed discounted recovery upgrade price `$135.00` (standard `$290.00`), generating `+$80.00` net margin. | **PASS** | `05-guardrailed-offer-bubble.png` (Offer bubble: `$135.00`) |
| **4** | Customer Agent guardrail check approves offer as optional courtesy | Re-evaluated guardrail rules: core disruption 100% resolved and Diamond courtesy verified; `guardrail_decision` approved. | **PASS** | `05-guardrailed-offer-bubble.png` (Timeline: `Approved`) |
| **4** | Zero-pressure WhatsApp delivery with Accept and No thanks choices | Delivered with label *"Optional. You can ignore this."*, clear pricing, and no countdown timer or coercive patterns. | **PASS** | `05-guardrailed-offer-bubble.png` |
| **5** | One-tap acceptance on WhatsApp | Mariana tapped `Accept`; button updated to `✓ Accept` and disabled further clicks. | **PASS** | `06-one-tap-accepted.png` |
| **5** | Payments API charge: $135 charged with offer idempotency key | Payments MCP processed charge for `$135.00`; `upgrade_confirmed` logged with `charge_id`. | **PASS** | `06-one-tap-accepted.png`<br>`07-closed-loop-verified.png` |
| **5** | Seat 2A Premium Business assigned & new boarding pass issued | Assigned seat `2A` in `premium_business`; released seat 14A; sent boarding pass attachment to WhatsApp. | **PASS** | `06-one-tap-accepted.png`<br>`07-closed-loop-verified.png` |
| **5** | Closed-loop verification verifies flight, seat, hotel, baggage, and upgrade | Verification specialist checked PSS, DCS, WorldTracer, Concierge, and Payments; all 5 checks returned `True`. | **PASS** | `07-closed-loop-verified.png` (Timeline: `Verification passed`) |
| **5** | Journey memory committed, CRM logged, NPS scheduled, journey CLOSED | State saved to SQLite Memory Bank, CRM interaction logged, NPS scheduled for +24h, status transitioned to `Closed`. | **PASS** | `07-closed-loop-verified.png` (Status: `Closed`) |

---

## 3. Edge Case & Non-Functional Testing

### 3.1. Edge Case 1: Infeasible Replanning (Santiago Connection Conflict)
- **Scenario:** After receiving the initial rebooking on AC1240, Mariana asks: *"Can I leave after 2pm?"*.
- **Observation:** The onward connecting flight `AC0800` departs Santiago at 14:00 (2:00 PM). The earliest flight departing after 2:00 PM would arrive in Santiago well after the connecting flight departs. The Customer Agent replied honestly:
  > *"I could not find a flight after 14:00 that protects your connection: AC1260 (13:10, arrives 14:00) would miss your onward connection. Your booking stays on AC1240, departing at 08:15. Tell me if you would like to look at other options."*
- **Outcome:** The journey remained safely on AC1240 with seat 14A preserved, and returned to `RESOLVED` status without stranding the passenger.
- **Status:** **PASS** (Screenshot: `docs/qa/screenshots/08-infeasible-replanning.png`)

### 3.2. Edge Case 2: "No thanks" (Decline Offer)
- **Scenario:** After replanning to AC1252 and receiving the optional $135 upgrade offer, Mariana clicks `No thanks`.
- **Observation:**
  - The offer button updated to `✓ No thanks` and disabled the `Accept` button.
  - The offer status was updated to `declined` with no follow-up nudges or pressure messages sent.
  - The closed-loop verification ran immediately: flight AC1252, seat 14A economy, baggage, and hotel verified; no payment charge was executed.
  - Analytics recorded `offer_declined`.
  - The journey transitioned cleanly to `Closed`.
- **Status:** **PASS** (Screenshot: `docs/qa/screenshots/09-offer-declined-no-thanks.png`)

### 3.3. Edge Case 3: Double-Tap on Offer Acceptance (Idempotency)
- **Scenario:** Passenger rapidly double-taps the `Accept` button on WhatsApp.
- **Observation:**
  - The UI immediately sets `answered` state on the first click, preventing second execution.
  - At the API level, `acme.customer.conversion.accept_offer` uses the offer ID as the idempotency key for the payment charge and verifies `any(r.status == 'accepted' for r in rec.state.offers)` to ensure idempotent returns.
  - Exactly one charge was processed, seat 2A was assigned once, and the journey closed cleanly.
- **Status:** **PASS** (Screenshot: `docs/qa/screenshots/10-double-tap-handled.png`)

### 3.4. Edge Case 4: Mid-Flow Refresh & State Recovery
- **Scenario:** Reloading `/console` and `/whatsapp` at various stages of disruption resolution.
- **Observation:**
  - **WhatsApp (`/whatsapp`):** Fully recovers message history from `GET /whatsapp/{id}/messages` and resumes polling cleanly. (Screenshot: `docs/qa/screenshots/11-refresh-chat.png`)
  - **Operations Console (`/console`):** On page refresh, the React state in `ConsolePage.tsx` resets to `null`, showing "No journey open". Attempting to click "Trigger AC1234 cancellation" again fails with an error because the backend already created the journey and ignores duplicate creations. (Filed as Defect `#105`).
- **Status:** **PASS-WITH-NIT** (Filed Issue `#105`)

### 3.5. Edge Case 5: Narrow Viewport (Mobile 375 × 667 px)
- **Scenario:** Rendering `/console` and `/whatsapp` on a mobile viewport (iPhone SE dimensions).
- **Observation:**
  - `/console`: The 2-column grid collapses to a clean single-column layout via `@media (max-width: 56rem)`. The timeline collapses from 3 columns to 2 columns via `@media (max-width: 36rem)`. No horizontal scrollbar or clipping observed.
  - `/whatsapp`: The chat container maintains `max-width: 24rem` with `width: 100%`, bubbles fit with generous padding, action buttons wrap without overflow, and the message input bar remains fixed at the bottom.
- **Status:** **PASS** (Screenshots: `docs/qa/screenshots/12-narrow-viewport-console.png`, `docs/qa/screenshots/13-narrow-viewport-whatsapp.png`)

### 3.6. Edge Case 6: Keyboard-Only Navigation & Accessibility
- **Scenario:** Operating both views using Tab, Shift+Tab, and Enter/Space only.
- **Observation:**
  - Tab order begins with `<a class="skip-link" href="#main">`, followed by navigation links (`Console`, `WhatsApp`), then into the primary panel actions.
  - All interactive elements exhibit visible focus outlines (`:focus-visible`).
  - Screen reader text (`.sr-only`) is present on message bubbles indicating sender identity (`You:` vs `ACME Airlines:`).
  - Quick-reply buttons are grouped inside `<div role="group" aria-label="Quick replies">` and can be navigated and triggered via keyboard.
- **Status:** **PASS** (Screenshots: `docs/qa/screenshots/14-keyboard-console-focus.png`, `docs/qa/screenshots/14-keyboard-chat-focus.png`)

---

## 4. Defects Filed

Three defects were filed during the exploratory testing sessions:

| Issue | Role | Title & Summary | Severity |
| :---: | :---: | :--- | :---: |
| **[#104](https://github.com/duboc/acme-airlines/issues/104)** | `role:backend` | **SalesRadar retains in-memory sent proposals across scenario resets**<br>When `POST /scenario/reset` is called, Customer runtime resets but the separate `acme-sales` service does not clear `SalesRadar._sent`. Subsequent runs of the scenario with the same journey/flight never receive an upsell proposal until `acme-sales` process is restarted. | **Major** |
| **[#105](https://github.com/duboc/acme-airlines/issues/105)** | `role:frontend` | **Operations console loses active journey state on page refresh**<br>`ConsolePage.tsx` does not fetch active journeys on mount via `GET /journeys`. Refreshing the page during a disruption resets the view to blank, and re-clicking "Trigger AC1234 cancellation" displays an unhandled error. | **Minor** |
| **[#106](https://github.com/duboc/acme-airlines/issues/106)** | `role:frontend` | **JourneyCard renders internal booking_id instead of partner hotel property code HT8842**<br>`JourneyCard.tsx` renders `hotel.booking_id` (e.g. `HB-AD0362EF, 1 night`) but omits `hotel.hotel_id` (`HT8842`), making it difficult for ops to verify the partner hotel specified in the brief. | **Minor** |

---

## 5. Visual Evidence Artifacts

The following exploratory session screenshots are captured under `docs/qa/screenshots/`:

| File | Context | Key Verification Point |
| :--- | :--- | :--- |
| `01-console-initial.png` | Operator Console | Initial console state before cancellation trigger |
| `02-backstage-resolved.png` | Operator Console | Zero-touch rebuild: AC1240, 14A, HT8842, dinner vouchers, sales held back |
| `03-whatsapp-first-message.png` | Simulated WhatsApp | Proactive message arriving at 10:09 PM with complete recovery details |
| `04-replanning-ac1252.png` | Simulated WhatsApp | Conversational replanning: "after 10am" -> AC1252 (11:40 AM) with connection kept |
| `05-guardrailed-offer-bubble.png` | Simulated WhatsApp | Guardrailed upgrade offer bubble ($135 lie-flat) after acknowledgement |
| `06-one-tap-accepted.png` | Simulated WhatsApp | One-tap conversion: Seat 2A confirmed and new boarding pass issued |
| `07-closed-loop-verified.png` | Operator Console | Closed loop: verification passed, memory committed, CRM logged, NPS scheduled, CLOSED |
| `08-infeasible-replanning.png` | Simulated WhatsApp | Infeasible replanning (> 2:00 PM) honest explanation protecting Santiago connection |
| `09-offer-declined-no-thanks.png` | Simulated WhatsApp & Console | "No thanks" decision: offer declined, closed loop runs, zero charges, status CLOSED |
| `10-double-tap-handled.png` | Simulated WhatsApp | Double-tap idempotency: single charge and single seat assignment executed |
| `11-refresh-console.png` | Operator Console | Mid-flow console refresh behavior |
| `11-refresh-chat.png` | Simulated WhatsApp | Mid-flow WhatsApp refresh maintaining full conversation transcript |
| `12-narrow-viewport-console.png` | Mobile (375 × 667) | Responsive single-column console layout |
| `13-narrow-viewport-whatsapp.png` | Mobile (375 × 667) | Responsive mobile WhatsApp chat interface |
| `14-keyboard-console-focus.png` | Accessibility | Visible keyboard focus on console controls |
| `14-keyboard-chat-focus.png` | Accessibility | Visible keyboard focus on quick-reply choice buttons |

---

## 6. Regression Sniff & Quality Checks

Adjacent features and test suites were checked during acceptance:
- **Backend Tests:** `uv run pytest -m "not live"` in `app/backend` passed cleanly.
- **Backend Hygiene:** `tests/unit/test_no_keys_no_regions.py` confirms no committed keys or regional Vertex fallback violations.
- **Frontend Unit Tests:** `npm test` in `app/frontend` (Vitest) passed cleanly.
- **Frontend Lint & Typecheck:** `npm run lint` and `npm run build` passed with zero errors.
- **End-to-End Regression:** Standard Playwright test `npm run e2e` (`e2e/narrative.spec.ts`) runs and passes in ~30s.

---

## 7. Not Tested (Deliberately Skipped)

- **Vertex AI Live Mode (`LLM_MODE=vertex`):** Live calls against global Vertex AI were deliberately skipped during exploratory testing to avoid incurring cloud project charges; `LLM_MODE=fake` was tested per the brief.
- **Docker Compose Container Networking:** Agent containers run without Docker daemon access; all acceptance testing was executed against the local multi-process dev architecture using identical ports, tokens, and environment variables as `compose.yaml`.
