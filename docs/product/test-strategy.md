# Test strategy

Owner: `acme-qa`. Companion to the MVP plan ([mvp-plan](mvp-plan.md)) and the product brief
([irrops-use-case](irrops-use-case.md)). Run everything from `app/backend` with `uv run pytest`.

## Test pyramid

| Layer | What it proves | Where | In CI |
|-------|----------------|-------|-------|
| 1. Unit / policy | Pure logic: domain, `connection_check`, `duty_of_care`, `margin_opt`, `guardrail_offer` | `tests/domain`, `tests/policy`, `tests/unit` | yes |
| 2. MCP contract | Each simulated system's tools, schemas and per-caller allowlist | `tests/sim` | yes |
| 3. Scenario | Brief scenarios 1–5 end to end through `ScenarioDriver` | `tests/scenario` | yes |
| 4. Governance | Sales never reaches the passenger; no offer before resolution (architecture, structured offers, TOCTOU races) | `tests/governance` (#22 part 1, #29 part 2) | yes |
| 5. UI E2E | Playwright narrative against the running stack (#23) | `app/frontend` | yes (`.github/workflows/e2e.yml`) |
| 6. Live smoke | Real Gemini on Vertex AI `global` | `-m live` | **opt-in**, factory VM only |

Test at the lowest layer that captures the behaviour. CI runs `pytest -m "not live"`; the
`live` marker is deselected by default (`addopts`).

## Determinism rules

- Time comes from an injected `FixedClock` (starts at 22:03 on D); tests move it with
  `advance_clock`. Never `datetime.now()` or `sleep`.
- `LLM_MODE=fake` (FakeLlm) in tests and CI: $0, no credentials, no network.
- Simulated systems are in-process MCP over the seeded `mariana_world()` fixture. No
  third-party calls.
- Schedule and fares live only in `acme.domain.fixtures`; tests assert the brief's
  concrete values, never recompute them.
- Each test builds its own driver (`reset()`); no shared mutable state.

## Scenario harness and `ScenarioDriver`

`tests/scenario/driver.py` defines the `ScenarioDriver` protocol (`reset`,
`trigger_cancellation`, `passenger_reply`, `passenger_tap`, `get_journey`, `transcript`,
`advance_clock`), the `Message` model and the `EventType` timeline names/payload keys the
tests assert. The runtime exposes `acme.runtime.scenario.make_driver(clock) -> ScenarioDriver`;
the `driver` fixture imports it. The driver runs the Sales radar in-process (as the `sales`
caller, through the Customer skills only) after every passenger-visible step, maps the button
labels to the harness ids (`accept_upgrade`, `decline_upgrade`) and routes taps to the #28
accept/decline flows.

Every scenario test is Given/When/Then. A test for a feature that has not landed is marked
`@needs(<issue>[, ...])` (`tests/scenario/marks.py`), which is
`xfail(strict=True, reason="needs #N")`. **At M4 (#91) no `@needs` remains**: `uv run pytest
tests/scenario` reports 0 xfailed and 0 xpassed.

### Flipping a test from xfail to pass

1. Implement the feature and `make_driver` support behind the driver protocol.
2. Run `uv run pytest tests/scenario`. Tests you completed report `XPASS(strict)` = **FAILED**.
3. Delete the `@needs(...)` line on exactly those tests (and the unused import) in the same PR.
4. CI is green when each scenario test passes or is strict-xfail for a still-open issue.
   If the driver contract must change, change `driver.py` in a PR reviewed by `acme-qa`.


## Governance suite (part 1, #22)

`tests/governance/` attacks each guardrail layer on its own:

| Layer | File | What it proves |
|-------|------|----------------|
| Architecture | `test_architecture.py` | Sales config/tools are `inventory` only; the Sales token gets 403 from every other registered server; `journey_status` has exactly four non-PII fields (incl. the opaque `run_id`) |
| Structured offers | `test_structured_offers.py` | No free-text field; extra keys (fuzzed), injected text in `seat_hint`/`flight`/`cabin` and text A2A parts are rejected |
| Claims vs inventory | `test_claims_vs_inventory.py` | Inflated standard price, false margin, zero inventory are rejected |
| Policy | `test_policy_properties.py` | hypothesis: approval implies all six rules (independent oracle, vacuity-guarded); each rule also hand-tested |
| State | `test_state.py` | Nothing proposed or emitted unless RESOLVED and acknowledged |

A real violation is filed as a bug and the test is `xfail(strict=True)` with the issue number
(currently #83), never weakened.

**Mutation check** (run locally, not in CI): remove rule 1 from `guardrail_offer.evaluate` → 7
governance tests fail; remove the radar's status/acknowledgement guard → 4 fail; let `Offer`
accept extra keys → 9 fail; drop the rule-6 origin check → 1 fails.

## Governance suite (part 2, #29)

`tests/governance/flow.py` drives the real Customer runtime, the real Sales radar (over the A2A
wire), `FakeLlm` (spied) and a `FixedClock`; `Flow.interleave(server, tool, action)` injects a
passenger/system action at an exact await point to reproduce time-of-check/time-of-use races.
(Flow uses the Customer runtime directly to test fine-grained interleavings; `ScenarioDriver.passenger_tap` drives button taps in scenario tests.)

| File | What it proves |
|------|----------------|
| `test_outcome.py` | No offer message before the *final* resolution is acknowledged; none on AC1240; exactly one offer after 5 ticks and a "No thanks"; Sales text never appears in the transcript or any LLM input |
| `test_races.py` | (a) change during proposal, (b) REPLANNING between approval and send, (c) accept after REPLANNING, (d) duplicate accepts, (e) 2A taken / inventory 0 / failure after charge, (f) concurrent proposals |

Finding #88 (accept re-checks guardrail after charge and before seat assignment; change requests take the journey lock) resolved and verified (0 xfail).

**Mutation check** (local, not CI): disabling the send-time re-check in `_deliver` (#15) fails
1 race test; disabling the accept-time re-check in `_convert` (#28) fails 2; replacing the
per-journey lock with a fresh lock fails the duplicate-accept test.

## Fact → test traceability

`test_harness.py` fails if a scenario test is missing from this table (or the table cites a
test that does not exist).

| Brief fact | Test |
|------------|------|
| S1: OCC cancels AC1234 at 10:03 PM (maintenance) | `test_s1_silent_ingestion.py::test_cancellation_is_detected_at_2203_from_the_occ_stream` |
| S1: Diamond, seat 14A, 1 checked bag | `test_s1_silent_ingestion.py::test_profile_evaluation_loads_diamond_tier_seat_14a_and_one_bag` |
| S1: onward connection 2:00 PM in Santiago | `test_s1_silent_ingestion.py::test_onward_connection_is_flagged_as_the_high_stakes_constraint` |
| S1: silent, does not alarm the passenger | `test_s1_silent_ingestion.py::test_ingestion_is_silent_no_passenger_message_is_sent` |
| S2: AC1240 08:15 nonstop | `test_s2_journey_rebuild.py::test_rebooked_on_ac1240_nonstop_at_0815` |
| S2: 14A held on new aircraft | `test_s2_journey_rebuild.py::test_seat_14a_is_held_on_the_new_aircraft` |
| S2: bag retagged to AC1240 | `test_s2_journey_rebuild.py::test_checked_bag_is_retagged_and_routed_to_ac1240` |
| S2: 2:00 PM connection preserved | `test_s2_journey_rebuild.py::test_santiago_connection_is_preserved_by_the_rebooking` |
| S2: hotel HT8842, transfer, dinner voucher | `test_s2_journey_rebuild.py::test_duty_of_care_books_ht8842_a_transfer_and_a_dinner_voucher` |
| S2: journey RESOLVED | `test_s2_journey_rebuild.py::test_journey_reaches_resolved_after_the_rebuild` |
| S2: no Sales pitch during resolution | `test_s2_journey_rebuild.py::test_sales_is_suppressed_while_the_disruption_is_being_resolved` |
| S3: WhatsApp at 10:09 PM | `test_s3_notification_replanning.py::test_whatsapp_message_is_sent_at_2209` |
| S3: message has cancellation and full resolution | `test_s3_notification_replanning.py::test_message_states_cancellation_and_the_completed_resolution` |
| S3: voucher attached, no offer yet | `test_s3_notification_replanning.py::test_message_attaches_the_dinner_voucher_and_has_no_offer` |
| S3: after 10:00 AM request → AC1252 11:40 | `test_s3_notification_replanning.py::test_after_10am_request_moves_the_passenger_to_ac1252_at_1140` |
| S3: 14A kept, connection re-verified | `test_s3_notification_replanning.py::test_replanning_keeps_seat_14a_and_reverifies_the_connection` |
| S3: itinerary updated in seconds | `test_s3_notification_replanning.py::test_replanning_completes_in_seconds_and_returns_to_resolved` |
| S4: no offer before acknowledged resolution | `test_s4_guardrailed_upsell.py::test_no_offer_before_the_passenger_acknowledges_the_final_resolution` |
| S4: 4 open Premium Business seats on AC1252 | `test_s4_guardrailed_upsell.py::test_sales_flags_four_open_premium_business_seats_on_ac1252` |
| S4: $135 vs $290 standard, +$80 margin | `test_s4_guardrailed_upsell.py::test_offer_prices_135_against_290_standard_for_80_net_margin` |
| S4: guardrail approves as courtesy | `test_s4_guardrailed_upsell.py::test_customer_agent_guardrail_approves_the_offer_as_a_courtesy` |
| S4: Sales only via Customer Agent | `test_s4_guardrailed_upsell.py::test_offer_reaches_the_passenger_only_through_the_customer_agent` |
| S4: offer after acknowledgement message | `test_s4_guardrailed_upsell.py::test_offer_is_sent_only_after_the_acknowledgement_message` |
| S5: boarding pass and hotel voucher via WhatsApp | `test_s5_conversion_verification.py::test_boarding_pass_and_hotel_voucher_are_delivered_over_whatsapp` |
| S5: one tap → seat 2A Premium Business ($135) | `test_s5_conversion_verification.py::test_one_tap_acceptance_confirms_seat_2a_premium_business_for_135` |
| S5: flight, seat, hotel, bag, upgrade verified | `test_s5_conversion_verification.py::test_verification_finds_flight_seat_hotel_baggage_and_upgrade_in_sync` |
| S5: Memory Bank, CRM, NPS, analytics | `test_s5_conversion_verification.py::test_journey_state_is_committed_to_memory_and_crm_nps_analytics_are_recorded` |
| S5: declining keeps 14A, closes without a charge | `test_s5_conversion_verification.py::test_declining_keeps_seat_14a_closes_the_journey_without_a_charge` |
| S5: no verification before passenger decides | `test_s5_conversion_verification.py::test_verification_does_not_run_before_the_passenger_decides` |
