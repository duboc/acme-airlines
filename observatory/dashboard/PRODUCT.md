# PRODUCT.md — ACME Observatory dashboard

## Register
Product (operator tool). Not marketing: no hero, no decoration that carries no data.

## Users and job
The product owner and operators who run the Scion software factory. They open the dashboard to answer,
in under ten seconds: *Is the factory healthy? What is it costing? Is the budget at risk?*
They return several times a day, often between other tasks, and need to trust what they see.

## What it shows
Seven read-only panels fed by `/api/*` (see `API.md`): cloud **Spend**, **Model** token use, **Agents**
fleet, **Agent Engine** (per-engine p50/p95 latency, error rate, requests and recent Cloud Trace links; if
trace access is not granted the metrics still show with a "Trace access not granted" note), **GitHub**
work flow and CI, **IRROPS product** (guardrail decisions by rule and outcome, offers, template fallbacks, needs_human, notification p50/p95, 5xx and hosted-demo health; metrics not yet reported read "No data yet", never zero), and **Budget** against guard thresholds. It never writes.

## Tone: dense, calm, legible
- Dense: many facts per screen, no scrolling to find the headline number.
- Calm: no alarm colours unless action is needed; one accent, used sparingly.
- Legible: tabular numerals, plain words ("Billing data is 3 days old"), no jargon in states.

## Principles
1. **Never lie by omission.** Every panel always says which of `ok / empty / lagging / unavailable` it is,
   in words, with the API `message`. No blank boxes, no endless spinners.
2. **Stale data is shown, with a warning.** `lagging` keeps the data visible and says how old it is.
3. **Status is never colour alone.** Pair colour with a text label and a shape.
4. **Each panel fails alone.** One endpoint down must not blank the others.
5. **Keyboard first.** Every control reachable and visible on focus; WCAG AA contrast and landmarks.

## Anti-references
Analytics-SaaS hero metrics, gradient cards, glassmorphism, auto-playing charts, red/green only status.

## Brand
ACME Airlines navy and a single signal orange, shared with the operations console (`/DESIGN.md`).
