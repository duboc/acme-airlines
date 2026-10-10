# ADR 0013: Autonomous factory loop, model tiers and cost controls

- **Status:** Accepted
- **Date:** 2026-10-09
- **Supersedes:** the model mappings in ADR 0010 and ADR 0011, and the
  human-approval gate for the MVP plan.

## Context

The product owner wants the factory to run unattended until the backlog is
done, coordinating through GitHub Issues, merging autonomously, and asking
humans only for more backlog. They also want lower cost and a hard spending
stop, while everything keeps working.

## Decision

- **Model tiers:**
  - Opus for coordination and planning (lead, eng-manager, architect)
  - Sonnet for coding (backend, frontend, qa)
  - Gemini Flash for everything else
- **Caps:** per-role `max_duration`/`max_turns`. At most 4 running agents
  besides the lead.
- **Loop:**
  - Driven by worker events, plus a 30-minute `acme-factory-tick` safety-net
    schedule.
  - A no-op tick when nothing changed.
  - Idle mode when the backlog is empty: suspend everyone and ask for
    backlog once.
- **Work bus:** GitHub Issues, using labels, `Depends on`, structured comments
  and a pinned "🏭 Factory status" issue.
- **Autonomous merge:** the latest `VERDICT: APPROVE` plus green CI.
- **Mocks and LLM mode:** every external system is mocked, and
  `LLM_MODE=fake` (the default for dev, tests and CI) keeps CI free of model
  spend.
- **Switch:** `infra/60-factory.sh` pause/resume. The VM is never stopped by tooling.
- **Budget:** $1000/month with alerts at 50/90/100%. At 100% of actual spend,
  `acme-budget-guard` **suspends all agents** through the hub (product-owner
  rule: **never stop the VM**; the budget is mostly tokens). The hub and UI
  stay up.

## Consequences

- No human gate before code reaches `main`. Quality rests on the review
  verdict, CI and the security auditor.
- Spend scales with open work and stops when the backlog is empty. At worst it
  is capped by the budget guard, though about a day late, since billing data
  lags.
- An auto-pause stops delivery until an operator runs
  `infra/60-factory.sh resume`.
