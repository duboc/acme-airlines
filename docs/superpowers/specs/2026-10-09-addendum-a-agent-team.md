# Addendum A — Agent Development Team ("the factory crew")

- **Date:** 2026-10-09
- **Status:** Draft (awaiting review)
- **Extends:** [2026-10-09-scion-factory-design.md](2026-10-09-scion-factory-design.md) — this
  replaces that spec's out-of-scope line "Role templates / multi-agent team workflows".

## 1. Intent

Run a team of Scion agents that works like a normal software team on
`duboc/acme-airlines`. A **lead agent** orchestrates the others. The first
goal is the product brief in
[`docs/product/irrops-use-case.md`](../../product/irrops-use-case.md).

### Success criteria
1. The hub project `acme-airlines` exists, with the `GITHUB_TOKEN` project secret.
2. Five role templates are versioned in this repo and resolvable by the hub.
3. The lead agent is running. Its first deliverable is a PR containing an MVP
   plan, a stack proposal (as an ADR) and a backlog of GitHub Issues. The human
   approves that plan before implementation starts.
4. The lead spawns workers per issue. Workers open PRs, and the reviewer agent
   reviews every PR. The lead merges only when **reviewer approval + green CI**
   are both present. The human can intervene at any time.

## 2. Team

| Role | Template | Harness | Model | Responsibilities |
|------|----------|---------|-------|------------------|
| Lead / tech lead | `acme-lead` | claude | Opus 5.5 (`large`) | Plans, writes ADRs, owns the backlog (GitHub Issues), spawns and messages workers, tracks progress, merges after approval + green CI. Writes no feature code. |
| Backend developer | `acme-backend` | claude | Sonnet 5.5 (`medium`) | Agent services, A2A/MCP integrations, APIs, domain logic, unit tests. |
| Frontend developer | `acme-frontend` | antigravity | Gemini Pro (`large`) | Operator console / passenger chat UI, UI tests. |
| QA / test engineer | `acme-qa` | antigravity | Gemini Pro (`large`) | Test strategy, scenario/e2e tests for scenarios 1–5, CI test gates, bug reports as issues. |
| Code reviewer | `acme-reviewer` | claude | Opus 5.5 (`large`) | Reviews every PR (correctness, security, guardrail rule "service advocacy > revenue"), using GitHub review approve / request-changes. Pushes no code. |

Reviews cross vendors: Gemini-authored PRs get a Claude review, and the
reviewer runs on Opus for Sonnet-authored PRs.

## 3. Design

- **Templates live in this repo** under `.scion/templates/acme-<role>/`, as project
  templates. Each has `scion-agent.yaml`, `system-prompt.md` and `agents.md`.
  They reuse the upstream `scion-frontiers/agent-team` skills by URI
  (`software-engineering-process`, `code-review`, `artifact-durability`,
  `agent-state-continuity`, addyosmani skills) and add acme-specific rules: team
  roster, harness/model per role, the PR/merge policy, issue conventions, and the
  "global Vertex only" rule.
- **Model pinning:** `model:` size aliases (`large`/`medium`) plus
  `default_harness_config` per template. Claude templates set
  `ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-5-5`, because the harness default
  `claude-haiku-4-5` is not enabled (verified 404).
- **Coordination:**
  - **Backlog:** GitHub Issues with labels `role:backend|frontend|qa`,
    `status:ready|in-progress|in-review|blocked`, and `mvp`.
  - **Dispatch:** the lead runs `scion start <name> --type acme-<role> "<issue #N …>"`
    from its container (agent-mode CLI), one worker per issue or small batch.
    Workers report back with Scion messages and `sciontool status`.
  - **Delivery:** branch `feat/<issue>-<slug>`, a PR that closes the issue, review
    by `acme-reviewer`, merge by the lead.
- **CI:** the lead's first implementation issue adds a GitHub Actions workflow
  (lint + tests), so "green CI" has a meaning.
- **Hub admin access (one-time, needs approval):**
  - **The problem:** in IAP proxy mode, the hub has no non-interactive way for
    the CLI to act as `admin@duboc.altostrat.com`. Projects and secrets have no
    user-access-token scopes, and dev mode would create resources owned by a
    pseudo-user.
  - **The fix:** temporarily enable hub federation that trusts Google-issued
    user tokens for domain `duboc.altostrat.com`, with audience = the gcloud CLI
    client. I then run `scion hub …` on the VM against `localhost:8080`, with
    `SCION_HUB_TOKEN=$(gcloud auth print-access-token)`. That runs as the same
    admin user that IAP signs in.
  - **Afterwards:** remove the block and restart the hub. IAP keeps guarding the
    public URL the whole time.
- **Secret handling:**
  - `GITHUB_TOKEN` is passed over SSH **stdin** into `scion hub secret set`. It
    is never put in argv, files, git or logs.
  - The human rotates it afterwards.

## 4. Prerequisites for the human

- The GitHub fine-grained token needs **Contents RW, Pull requests RW, Issues RW,
  Workflows RW** (the workflow scope is needed to push `.github/workflows/*`) on
  `duboc/acme-airlines`.
- Sign in to the hub web UI once, so the admin user record exists before
  federation maps tokens onto it.

## 5. Tasks (mini-plan)

1. **Hub admin access:**
   - check: `infra/verify.sh hubproject` RED
   - add the federation block via `infra/30-hub-admin-access.sh enable|disable`
     (idempotent; backs up `settings.yaml`)
   - confirm `scion hub status` works as admin
2. **Project + secret:** `scion hub project create …`, then
   `scion hub secret set --project acme-airlines GITHUB_TOKEN` from stdin.
   Expect `hubproject` → PASS.
3. **Smoke test (original Task 6):** one antigravity and one claude agent each
   open a smoke PR. Expect `smoke` → PASS. Confirm the Claude env has
   `CLOUD_ML_REGION=global` and the haiku override.
4. **Team templates:**
   - write `.scion/templates/acme-*` and merge them to `main`
   - check: `scion templates list --project acme-airlines` shows all 5
   - add `verify.sh team` (templates resolvable)
5. **Launch the lead** with a kickoff prompt: read the brief, open a planning PR
   (ADR stack + MVP plan + backlog issues), wait for human approval, then
   dispatch. Add `verify.sh lead` (lead agent running).
6. **Disable federation** (`30-hub-admin-access.sh disable`). Write the docs:
   `docs/factory/team.md` (how to talk to the lead, attach, steer, stop) and the
   runbook steps. Final review, then push.
