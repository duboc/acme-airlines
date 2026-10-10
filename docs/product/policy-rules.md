# Policy rules

Pure, deterministic policy modules in `app/backend/src/acme/policy/`. No LLM and no I/O are
involved in any policy decision. Duty-of-care research and full citations:
[docs/research/duty-of-care.md](../research/duty-of-care.md) (#31). Not legal advice.

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
other origins raise `ValueError`). `wait_start` is the local time the wait begins (22:03 for
Mariana). Every entitlement carries a cited reason in `rule_reasons`, including when it is
not granted (`(threshold not met)`).

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
