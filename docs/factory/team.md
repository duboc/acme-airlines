# The factory crew — how to work with the agent team

The team is thirteen Scion role templates, generated from `factory/team/` by
`infra/40-build-team-templates.sh` into `factory/templates/acme-*` and pushed to
the hub at **global** scope. Design: [addendum A](../superpowers/specs/2026-10-09-addendum-a-agent-team.md).

| Role | Template | Harness / model | Hub agent role | Cap (`max_duration` / `max_turns` / `max_model_calls`) |
|------|----------|-----------------|----------------|-----|
| Lead / tech lead | `acme-lead` | claude / **Opus** | `full` | — |
| Engineering manager | `acme-eng-manager` | claude / **Opus** | `full` | 4h / 800 / 2400 |
| Architect | `acme-architect` | claude / **Opus** | `baseline` | 1h / 200 / 600 |
| Backend developer | `acme-backend` | claude / **Sonnet** | `baseline` | 2h / 400 / 1200 |
| Frontend developer (+ **impeccable**) | `acme-frontend` | claude / **Sonnet** | `baseline` | 2h / 400 / 1200 |
| QA / test engineer | `acme-qa` | claude / **Sonnet** | `baseline` | 2h / 400 / 1200 |
| Code reviewer | `acme-reviewer` | antigravity / **Gemini Flash** | `baseline` | 45m / 150 / 450 |
| Security auditor | `acme-security-auditor` | antigravity / **Gemini Flash** | `baseline` | 45m / 150 / 450 |
| QA tester (Playwright) | `acme-qa-tester` | antigravity / **Gemini Flash** | `baseline` | 45m / 150 / 450 |
| Investigator | `acme-investigator` | antigravity / **Gemini Flash** | `baseline` | 30m / 100 / 300 |
| Researcher | `acme-researcher` | antigravity / **Gemini Flash** | `baseline` | 30m / 100 / 300 |
| Documentation writer | `acme-doc-writer` | antigravity / **Gemini Flash** | `baseline` | 30m / 100 / 300 |
| Release engineer | `acme-release-notes` | antigravity / **Gemini Flash** | `baseline` | 30m / 100 / 300 |

The lead runs at most **4 agents** at once besides itself, counting
eng-managers and their workers. When to use which role is in
`factory/team/roles/lead.md`.

Claude templates set `ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-5-5`, because
the harness default `claude-haiku-4-5` is not enabled on Vertex. All model
traffic goes to Vertex AI `global` using the dedicated `acme-agent@` service
account (`assign` mode).

## How the team works

```mermaid
flowchart LR
  po["Product owner (you)"] -- "approve plan / steer" --> lead["acme-lead (Opus)"]
  lead -- "GitHub Issues backlog" --> gh[("duboc/acme-airlines")]
  lead -- "scion message --wake" --> be["acme-backend (Sonnet)"] & fe["acme-frontend (Sonnet)"] & qa["acme-qa (Sonnet)"]
  be & fe & qa -- "PR per issue" --> gh
  lead -- "wake reviewer per PR" --> rev["acme-reviewer (Gemini Flash)"]
  rev -- "gh pr review (VERDICT:)" --> gh
  lead -- "merge after VERDICT: APPROVE + green CI" --> gh
  lead -- "status issue + alerts" --> po
```

1. **Phase 0 (planning).**
   - The lead opens a PR on `docs/mvp-plan` with the stack ADR
     (`docs/decisions/0100-app-stack.md`), `docs/product/mvp-plan.md` and the
     MVP issues.
   - A reviewer agent reviews it.
   - The lead messages you, then **waits for your approval**: approve or merge
     the PR, or message the lead.
> [!NOTE]
> All agents authenticate to GitHub with one token (`GITHUB_TOKEN`), so GitHub's
> native review state cannot separate author from reviewer. Instead:
> - **Verdict comments:** `acme-reviewer` posts a review whose first line is
>   `VERDICT: APPROVE` or `VERDICT: REQUEST CHANGES`.
> - **Branch protection on `main` (`infra/35-github-repo.sh`):** enforces PR-only
>   merges (`enforce_admins=true`) and 4 required status checks (`backend`,
>   `engine-smoke`, `frontend`, `pr-size`).
>
> See [ADR 0006](../decisions/0006-github-fine-grained-pat.md) and [ADR 0016](../decisions/0016-factory-audit-hardening.md).

2. **Delivery loop.**
   - The lead runs at most 4 workers at once.
   - Each issue gets one branch and one PR, reviewed by `acme-reviewer`.
   - The lead merges only with `VERDICT: APPROVE` **and** green CI.

## The autonomous loop

- **Work bus.** Everything happens in **GitHub Issues**. Read the pinned
  issue **🏭 Factory status** for the live picture.
- **Labels.** `status:*`, `agent:*`, `priority:*` and `role:*` track each
  issue.
- **Comments.** Agents write structured `[agent] STATUS/DONE/NEXT/BLOCKER`
  comments, and file new work as `status:triage`.
- **Wake-ups (4-layer heartbeat).** The lead wakes on immediate worker events
  (`scion message acme-lead`), on the hourly wake-on-change watchdog
  (`acme-factory-watchdog`, `0 * * * *`), on daily `04:00 UTC` reincarnation
  (`acme-lead-reincarnate`), and on the 6-hour safety-net schedule
  (`acme-factory-tick`, `0 */6 * * *`). Each pass, it merges what's approved
  and green, triages, and dispatches.
- **Your inbox.** The lead messages you **only** when the backlog is empty.
  Send more backlog by filing issues or messaging `acme-lead`.
- **Cost.** `infra/60-factory.sh pause|resume` (the VM is never stopped), plus the $2,000
  budget guard (see [operations](../setup/02-operations.md)).

## Seeing the topology in the UI

Open the hub, then **Projects → acme-airlines → Agents** and switch to the
**graph** view (the lineage graph). You can also use the top-level **Agents**
page. Press `t` to flip between vertical and horizontal, and `f` to fit.

```mermaid
flowchart TD
  you["admin@duboc.altostrat.com"] --> lead["acme-lead (orchestrator)"]
  lead --> arch[acme-architect] & be[acme-backend] & fe[acme-frontend] & qa[acme-qa] & qat[acme-qa-tester] & rev[acme-reviewer]
  lead --> sec[acme-security-auditor] & inv[acme-investigator] & res[acme-researcher] & doc[acme-doc-writer] & rel[acme-release-notes]
  lead -. "ephemeral extras" .-> be2["acme-backend-2 …"]
  lead -. "optional workstream" .-> em["acme-eng-manager-*"] -.-> w["its workers"]
```

How the tree stays in place:

- **Lineage is automatic.** An agent's parent is whoever started it. The lead
  starts every `acme-*` agent, so all of them sit under it. An eng-manager's
  workers sit one level below the eng-manager.
- **Standing roster.** There is one persistent agent per role, named exactly
  like its template.
  - Idle agents are **suspended** (or **stopped**, if their harness can't
    suspend). They use no CPU but stay in the graph.
  - The lead assigns work with `scion message --wake <agent> "<issue>"`.
  - Standing agents are never deleted.
- **Ephemeral extras** (`acme-<role>-2…`) appear for parallel work and
  disappear when their PR merges.
- **Labels.** Every agent carries `acme.team/role=<role>` and
  `acme.team/tier=standing|ephemeral`, so you can filter by them in the UI.
- **Check.** `infra/verify.sh topology` confirms that every live `acme-*`
  agent descends from `acme-lead`, and that at least 11 exist.

If an agent shows up **outside** the lead's tree, someone started it directly.
Ask the lead to adopt the work, then delete the stray agent.

## Talking to the team

**Web UI:** <https://scion-hub-acme-iap-proxy-vdhcml5ceq-uc.a.run.app>, project
**acme-airlines**. From there you can open the lead, read its terminal, send it
messages and see every agent.

**Your inbox:** the lead sends you messages (`@admin@duboc.altostrat.com`).
Reply in the web UI conversation.

**Steering examples** (send to `acme-lead`):
- "Approved — start the delivery loop."
- "Scope MVP to scenarios 1–3 first; defer the upsell."
- "Pause all workers." / "Resume."

**CLI** (for operators, from the laptop): enable admin access, then:

```bash
infra/30-hub-admin-access.sh enable
bash -c 'source infra/lib.sh; hub_cli list --project duboc-acme-airlines'
bash -c 'source infra/lib.sh; hub_cli look acme-lead --project duboc-acme-airlines'
bash -c 'source infra/lib.sh; hub_cli message acme-lead --project duboc-acme-airlines "status?"'
infra/30-hub-admin-access.sh disable
```

## Changing the team

1. Edit `factory/team/team-rules.md` or `factory/team/roles/<role>.md`, or
   change the role table in `infra/40-build-team-templates.sh`.
2. Run `infra/40-build-team-templates.sh` and commit the regenerated
   `factory/templates/acme-*`.
3. Push to the hub with `infra/45-push-templates.sh` (which opens and closes
   admin access automatically and pushes every `acme-*` template at global
   scope).
4. New agents pick up the change. Running agents keep their old instructions;
   ask the lead to recycle them.

## Stopping everything

Ask the lead to stop all workers. Or, as operator:
`hub_cli stop <agent> --project duboc-acme-airlines` / `hub_cli delete <agent> …`.
To stop all token spend, run `infra/60-factory.sh pause`; the VM and hub stay up (see [operations](../setup/02-operations.md)).
