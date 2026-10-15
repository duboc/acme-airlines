# Policy rules

Pure, deterministic policy modules in `app/backend/src/acme/policy/`. No LLM and no I/O are
involved in any policy decision. Full statutory duty-of-care citations are carried directly in
`acme.policy.duty_of_care` and the table below (#31). Not legal advice.

## connection_check

`check_connection(airport, arrival, onward_departure, mct_minutes=None)` and
`check_flights(arriving, onward, mct_minutes=None)` return `feasible`, `connection_minutes`,
`mct_minutes` and `margin_minutes` (= connection - MCT; negative means infeasible; exactly
the MCT is feasible). MCT is per airport (`DEFAULT_MCT_MINUTES`; SCL international = 90,
unknown airports fall back to 90) and overridable per call. Datetimes must be timezone-aware.

Fixture check (D+1, SCL, MCT 90, onward AC0800 at 14:00): AC1240 arr 10:15 (margin 135),
AC1252 arr 12:20 (margin 10) are feasible; AC1260 arr 14:00 (margin -90) is not.

## duty_of_care

`evaluate_duty_of_care(...)` applies **ANAC Resolution 400/2016** (departures from Brazil;
other origins raise `ValueError`). `rights.assess(...)` (`acme.customer.specialists.rights`)
computes `wait_begin = min(detected_at, original_departure).astimezone(tz)` in the origin
airport's timezone and derives `wait_hours = max((new_local - wait_begin).total_seconds() / 3600, 0)`,
`wait_start = wait_begin.time()` (22:03 for Mariana), and
`requires_overnight = new_local.date() > wait_begin.date()` (#396). `duty.run_duty_of_care`
(`acme.customer.specialists.duty`) reads `origin_resident` and `special_needs` from
`crm.profile.get` (#324), `concierge.book_transfers` books round-trip `airport_to_hotel` +
`hotel_to_airport` legs when `hotel_required=True` (`DOC-05`, `DOC-07`) or a single
`airport_to_home` (`origin -> "HOME"`) leg when `hotel_required=False` (`DOC-06`), and
`outreach` (`collect_facts`, `required_facts`, `template_text`, `send_notification`) requires
and attaches `hotel_voucher` / `meal_voucher` / transfer mentions only for entitlements
actually granted (#324). Every entitlement carries a cited reason in `rule_reasons`, including
when it is not granted (`(threshold not met)`).

| Rule | Condition | Entitlement | Citation |
|---|---|---|---|
| DOC-01 | wait >= 1h | communication | ANAC 400 Art. 27, I |
| DOC-02 | wait >= 2h, wait starts 11:00-15:00 | lunch voucher | ANAC 400 Art. 27, II |
| DOC-03 | wait >= 2h, wait starts 18:00-23:00 | dinner voucher | ANAC 400 Art. 27, II |
| DOC-04 | wait >= 4h and overnight | add breakfast voucher | ANAC 400 Art. 27, II |
| DOC-05 | wait >= 4h, overnight, not origin resident | hotel + round-trip transfer | ANAC 400 Art. 27, III |
| DOC-06 | wait >= 4h, overnight, origin resident | transfer home only, no hotel | ANAC 400 Art. 27, § 1º |
| DOC-07 | wait >= 4h, no overnight, special needs (PNAE) | hotel + transfer | ANAC 400 Art. 27, § 2º |
| DOC-08 | wait < 4h, or >= 4h with no overnight and no PNAE | no hotel, no transfer | ANAC 400 Art. 27, III |
| DOC-09 | cancellation, or wait >= 4h | free re-accommodation | ANAC 400 Art. 21, I & Art. 28 |
| DOC-10 | technical maintenance and wait >= 4h | compensation-eligible flag | CDC Art. 14 / STJ REsp 1.584.465 (fortuito interno) |
| DOC-11 | force majeure | not compensation-eligible (care still due) | CDC Art. 14, § 3º, II; ANAC 400 Art. 26, § 3º |

Simplifications: categorical vouchers (no amounts); the compensation flag is informational
(Brazilian law sets no fixed cancellation tariff); outside the lunch and dinner windows no
single-meal voucher is issued; EU261 is not applied on a third-country route (Art. 3(1)), and a
route touching an EU airport is flagged for review in `rule_reasons["eu261"]`. If a
passenger is both an origin resident and PNAE with an overnight wait, DOC-06 applies.

Mariana (cancelled 22:03, re-accommodated next morning, ~10.2h, overnight, non-resident):
communication, dinner + breakfast, hotel + transfer, re-accommodation, compensation flag.

## Quiet hours (Customer agent, `acme.customer.quiet_hours`)

Default window 22:00-07:00 passenger-local, configurable (`QuietHours(start, end)`).

- During quiet hours the system sends **nothing** to the passenger until the journey has first reached `RESOLVED`.
- It then sends **exactly one** consolidated resolution message (#13).
- Passenger-initiated replies may always be answered.
- Offers in quiet hours are allowed only after the passenger acknowledges the final resolution (guardrail rule 2, #6).

`can_send_proactive(journey, now, quiet, passenger_initiated)` implements the gate (a journey that
later moves to `REPLANNING` still counts as having been resolved). Scenario 1 ingestion at 22:03
sends no message at all.

## margin_opt

`acme.policy.margin_opt` calculates the recovery upgrade price for courtesy-eligible tiers. Pure, deterministic, no LLM.

Formula (all amounts USD, `Decimal`):

    discounted = standard_fare * (1 - discount_pct[tier] / 100)
    price      = ceil_to_5(discounted)            # whole-$5 price points
    price      = clamp(price, cost_floor, standard_fare)
    net_margin = price - cost_floor

Tier discount table (`RECOVERY_DISCOUNT_PCT`):
- `diamond`: 55% off (Mariana: $290 standard → $130.50 → $135 price, cost floor $55, net margin +$80)
- `gold`: 40% off
- `silver`, `basic`: not eligible for recovery discount

## guardrail_offer

`evaluate(journey, offer, inventory)` in `acme.policy.guardrail_offer` implements the core "Two Goals, One Guardrail" policy. An offer from Sales is approved only if **all six rules** hold:

1. **`disruption_resolved`:** Disruption status is `RESOLVED`.
2. **`resolution_acknowledged`:** Passenger has acknowledged the current resolution (`resolution_acknowledged = True`) and has no open requests pending (`guardrail_offer` rejects with `open_request_pending` if `open_requests` is non-empty). When an inbound reply escalates to `needs_human` (`Other` intent), `outreach.handle_inbound` appends the `PassengerRequest` to `open_requests`, resets `resolution_acknowledged = False`, and invalidates any live offers (`status = "invalidated"`), while `CustomerSkills` reports `ineligible_reason = "open_request_pending"` (`sales_suppressed` with `reason: "open_request_pending"`) and `resolution_acknowledged = False` on `journey_status` (#325).
3. **`genuine_discount`:** Offer standard price matches inventory, inventory has open seats in the offered cabin, offer price is strictly below standard fare and at or below tier recovery price, and passenger tier is courtesy-eligible.
4. **`margin_verified`:** Net margin is non-negative and equals `price - cost_floor` recomputed from current inventory.
5. **`zero_pressure`:** Offer is optional (`optional = True`), and no other live or answered offer exists for this journey (none in `approved`, `sent`, `accepted`, `declined`, and none `invalidated` on the same flight).
6. **`origin_and_flight_verified`:** Offer origin is `"sales"` and offer flight matches the journey's current flight.

The guardrail is evaluated at proposal time, and re-evaluated in full at send time and accept time under the per-journey lock (`CustomerRuntime.journey_lock`).


## Service closure without an offer (#229)

Service advocacy outranks revenue, so closing a journey must not depend on the revenue path. A journey
that is `RESOLVED`, acknowledged by the passenger, has no open request and **no offer in play** (none
proposed/approved/sent, none decided) is verified and closed once the **offer window** has passed since
the acknowledgement (`CustomerRuntime.offer_window`, default 10 minutes on the injected `Clock`): flight,
seat, hotel (`verify_journey` checks a matching `sim.partners.hotel_bookings` record when
`hotel_required=True` under `DOC-05`/`DOC-07`, or verifies `rec.hotel is None` when
`duty_of_care_assessed.hotel_required` is `False` under `DOC-06`/`DOC-08`, #324), baggage and "no upgrade,
nothing charged" are verified, then the CRM log, the NPS prompt, the
analytics record (`event: no_offer`, `converted: false`), `CLOSED` and the Memory Bank commit, preceded by
a `service_closure` timeline entry. It runs from `CustomerRuntime.tick()` under the per-journey lock, so
it never waits on Sales and concurrent polls close it once. It covers every no-offer branch: Sales down or
suppressed, no premium inventory or seats, ineligible tier, guardrail rejection. A sent offer awaiting the
passenger is never closed over (the accept/decline flow closes it). A failed verification leaves the
journey `RESOLVED` (`verification_failed`) and retries after another window, not on every poll. After
closure the guardrail rejects any late proposal (`disruption_not_resolved`) and Sales no longer lists the
journey.
