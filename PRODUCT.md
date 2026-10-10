# PRODUCT.md — ACME Airlines IRROPS operations console

_Inferred from the issue brief and `docs/product/irrops-use-case.md` (no interactive interview was possible; assumptions are labelled)._

## Register
Product / **Operate** mode. Brand lives in precise details, not decoration.

## Users
- **Operations controllers (OCC / customer-care duty staff):** watch a disruption unfold, see what each agent decided and why, verify guardrail checks. Work in a dim-to-bright control room, long shifts, interrupted often. _(assumption)_
- **Demo audience / product owner:** needs to follow Mariana's AC1234 story in seconds.
- **Simulated passenger (Mariana):** the `/whatsapp` view, a phone-sized chat. Clearly labelled as simulated.

## Purpose
Show a flight cancellation being handled before the passenger notices: the OCC event, the Customer agent's journey rebuild, the A2A negotiation, each guardrail decision with its reasons, and the passenger conversation. Service advocacy outranks revenue; a Sales offer must never appear before the disruption is resolved and acknowledged.

## Tone — "calm under disruption"
Steady, plain, precise. No alarmism, no celebratory flourish. State what happened, what was decided, and what happens next. Never blame or hype.

## Brand (ACME)
Deep navy and signal-orange pairing from airline livery. _(assumption — no brand guide in repo)_ Orange is rare: it marks the one thing needing attention.

## Principles
1. Scanability beats expression: the controller finds the current state in under three seconds.
2. Every decision shows its reason.
3. Status is never colour alone.
4. Simulated data is always labelled as simulated.
5. WCAG AA, full keyboard use, works from phone to wide monitor.

## Anti-references
Neon "mission control" dashboards, hero-metric tiles, chatbot-purple gradients, anything urgent-red by default.
