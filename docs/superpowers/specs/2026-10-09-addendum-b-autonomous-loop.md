# Addendum B: Autonomous factory loop, cost controls, full-brief MVP

- **Date:** 2026-10-09
- **Status:** Approved by the product owner (2026-10-09 22:07)
- **Extends:** addendum A. This **supersedes** A's model mapping, A's merge
  gate (human approval of the plan), and ADR 0011's role models.

## Requirements (product owner)
1. Keep the factory running until all issues are done. GitHub Issues is how
   agents interact on tasks and next steps.
2. Merge everything autonomously. Only ask the human for **more backlog**.
3. **MVP = the full IRROPS brief** (`docs/product/irrops-use-case.md`,
   scenarios 1–5 plus governance). Mock every interface that's needed.
4. The frontend agent uses the **impeccable** skill (pbakaus/impeccable).
5. Cut cost but keep everything working:
   - **Opus** for coordination and planning
   - **Sonnet** for coding
   - **Antigravity Flash** for everything else
6. Budget: **$1000/month**, email alerts at 50/90/100%, **auto-pause of agents
   at 100%**. **Never stop the VM**; the budget is mostly token consumption.

## Design

### Models
| Group | Roles | Harness / model alias |
|---|---|---|
| Coordination & planning | lead, eng-manager, architect | claude / `large` (Opus 5.5) |
| Coding | backend, frontend, qa | claude / `medium` (Sonnet 5.5) |
| Everything else | reviewer, security-auditor, investigator, researcher, qa-tester, doc-writer, release-notes | antigravity / `medium` (Gemini Flash) |

### GitHub Issues as the work bus
- **Labels:**
  - `status:triage|ready|in-progress|in-review|blocked`
  - `agent:<standing-agent>` for ownership
  - `priority:p0|p1|p2`
  - `Depends on #N` in the issue body for dependencies
- **Structured comments:** `[<agent>] STATUS: … / DONE: … / NEXT: … / BLOCKER: …`.
  The next agent reads the thread.
- **New work** found by any agent is filed as an issue with `status:triage`.
- **Status page:** the pinned issue **"🏭 Factory status"** is rewritten by the
  lead each tick.

### Loop
- **Tick:** a recurring hub schedule `acme-factory-tick` (cron `*/30 * * * *`,
  UTC) sends the message `TICK` to `acme-lead`. The schedule is owned by the
  admin user.
- **Each tick, the lead:**
  1. sweeps agents
  2. reconciles issue and PR state
  3. merges whatever is ready
  4. triages new issues
  5. wakes standing agents for ready issues (at most 4 running)
  6. updates the status page
  7. goes back to `blocked`
- **Workers drive the loop.** They message the lead on done, blocked or PR
  ready (event-driven). The tick is only a safety net.
- **No-op ticks:** if nothing changed since the last tick, the lead replies
  with one line.
- **Idle mode:** when no open issues remain, the lead suspends all running
  agents, messages the product owner **once** for more backlog, and treats
  ticks as no-ops until new issues appear.
- **Between ticks:** scheduled messages can't wake suspended agents, so the
  lead stays running (in `sciontool status blocked`).

### Merge gate (autonomous)
- **Merge requirement:** the latest reviewer `VERDICT: APPROVE` **plus** green
  CI. No human approval.
- **Red CI or `REQUEST CHANGES`:** the PR goes back to its author.
- **Broken `main`:** a `priority:p0` fix issue.
- **Security findings:** blocking issues.
- **Reporting:** everything goes to the status page.

### MVP and mocks
- **Coverage:** every scenario and governance rule in the brief has an issue
  with Given/When/Then acceptance criteria.
- **Mocked systems:** all external systems are deterministic mocks seeded with
  Mariana's AC1234 story: OCC, PSS, DCS, CRM, Loyalty, WorldTracer, WhatsApp,
  Payments, Memory Bank, hotel and transfer booking, boarding-pass/voucher
  issuing.
- **LLM modes:** `LLM_MODE=fake` (default: dev, tests, CI) and `LLM_MODE=vertex`
  (live demo, Gemini on `global`).

### Impeccable
- `acme-frontend` gets the skill
  `gh://pbakaus/impeccable/.claude/skills/impeccable@d631a8827f99414d2b6daba4ef08b7f8701751d7`
  (Apache-2.0, v4.5.2).
- On the first frontend issue it creates `PRODUCT.md` and `DESIGN.md`, then
  uses impeccable for design, critique and polish.

### Cost controls
- **Per-template caps** (`max_duration` / `max_turns`; lead uncapped):
  - coders: `2h` / 400 turns
  - reviewer, security-auditor, qa-tester: `45m` / 150
  - other Flash roles: `30m` / 100
  - architect: `1h` / 200
  - eng-manager: `4h`
- **Concurrency:** at most 4 running agents besides the lead.
- **Operator switch:** `infra/60-factory.sh pause|resume|status`. Product-owner
  rule: **never stop the VM**.
  - `pause` pauses the tick schedule and suspends all agents; sessions,
    roster and graph are kept.
  - `resume` reverses it.
- **Budget:** $1000/month on the project, with email alerts at 50/90/100%.
  - At 100%, a Pub/Sub notification triggers the Cloud Run service
    `acme-budget-guard`, which **suspends all agents** through the hub.
    Token spend stops; the VM, hub and UI keep running.
  - Resume with `infra/60-factory.sh resume`.

## Tasks
1. **Templates:**
   - new model mapping, caps, the impeccable skill on frontend
   - role text for the Issues bus, loop, autonomous merge, idle mode, mocks
   - `verify.sh skills` (impeccable on the hub frontend template) and
     `verify.sh cost` (caps present), with unit tests for the parsers
2. **Factory control:** `infra/60-factory.sh` plus the tick schedule.
   `verify.sh loop` checks the schedule is active and its cron interval is at
   least 30 minutes.
3. **Budget guard:** `infra/70-budget-guard.sh` plus the function in
   `infra/budget-guard/`. `verify.sh budget` checks the budget exists and has
   the 1.0 threshold plus the Pub/Sub topic.
4. **Rollout:**
   - push templates
   - the lead recreates the 11 standing agents under the new templates (same
     names; harness changes need recreation)
   - the lead merges PR #25, aligns the backlog to the brief, creates the
     status issue and starts the loop
5. **Docs:** runbook §7, team.md, operations (pause/resume/budget), and
   ADR 0013. Final `verify.sh all`.
