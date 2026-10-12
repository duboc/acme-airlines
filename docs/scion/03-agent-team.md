# 3. The agent team

## Roles, models and caps

| Role | Template | Harness / model | Hub role | Cap (`max_duration` / `max_turns`) |
|---|---|---|---|---|
| Lead (orchestrator) | `acme-lead` | claude / Opus | `full` | none |
| Engineering manager (one workstream) | `acme-eng-manager` | claude / Opus | `full` | 4h / 800 |
| Architect | `acme-architect` | claude / Opus | baseline | 1h / 200 |
| Backend | `acme-backend` | claude / Sonnet | baseline | 2h / 400 |
| Frontend (+ impeccable skill) | `acme-frontend` | claude / Sonnet | baseline | 2h / 400 |
| QA, automated tests | `acme-qa` | claude / Sonnet | baseline | 2h / 400 |
| Code reviewer | `acme-reviewer` | antigravity / Gemini Flash | baseline | 45m / 150 |
| Security auditor | `acme-security-auditor` | antigravity / Gemini Flash | baseline | 45m / 150 |
| QA tester (exploratory, Playwright) | `acme-qa-tester` | antigravity / Gemini Flash | baseline | 45m / 150 |
| Investigator | `acme-investigator` | antigravity / Gemini Flash | baseline | 30m / 100 |
| Researcher | `acme-researcher` | antigravity / Gemini Flash | baseline | 30m / 100 |
| Doc writer | `acme-doc-writer` | antigravity / Gemini Flash | baseline | 30m / 100 |
| Release notes | `acme-release-notes` | antigravity / Gemini Flash | baseline | 30m / 100 |

The rule of thumb is **Opus for coordination and planning, Sonnet for code,
Flash for everything else.** Review is cross-vendor: Flash reviews code that
Sonnet wrote.

## How the templates are built

```text
factory/team/team-rules.md      shared rules (roster, Issues bus, merge gate, mocks, global-only Vertex)
factory/team/roles/<role>.md    role header (what this role does in THIS product)
          +  upstream scion-frontiers/agent-team@AGENT_TEAM_SHA (agents.md, system-prompt.md, skills)
          =  factory/templates/acme-<role>/{scion-agent.yaml, agents.md, system-prompt.md, skills/}
```

- **Generator:** `infra/40-build-team-templates.sh`. The `ROLES=( … )` table
  sets each role's upstream template, harness, model alias, caps, an extra
  skill, and the description.
- **Skills:** upstream skills are referenced by `gh://owner/repo/<name>` URIs.
  That shorthand only resolves `skills/<name>` at the repo root. A skill that
  lives elsewhere (impeccable's `.claude/skills/impeccable`) is **vendored as
  a template-mounted skill** under `skills/` (`vendor_skill`), with its
  LICENSE and NOTICE.
- **Claude haiku override:** the Claude templates set
  `ANTHROPIC_DEFAULT_HAIKU_MODEL` to an enabled model.
- **Checks:** `verify.sh cost` asserts the model mapping and caps. `verify.sh
  skills` asserts the mounted `SKILL.md` exists in the **hub-stored** template,
  not just a string in a file.

### Changing the team
1. Edit `factory/team/*`, or the `ROLES` table.
2. Run `infra/40-build-team-templates.sh` and commit `factory/templates/`.
3. Push to the hub (runbook step 7).
4. Tell the lead. Running agents keep their old instructions; the lead
   recreates standing agents under the **same names**. A harness or model
   change *requires* recreation.

## Topology: the standing roster

The hub's **Agent Lineage Graph** (Project → Agents → graph view) draws
parent→child from the agent's `ancestry`, meaning whoever started it. To make
the team visible as a tree under the lead:

- **Only the lead starts agents.** The exceptions are eng-managers, which
  start their own workers, so those appear one level deeper.
- **One standing agent per role, named like its template.** When idle it is
  suspended (or stopped if the harness can't suspend), never deleted. Hub
  agents are hard-deleted, so deleted agents vanish from the graph.
- **Extras are temporary:** ephemeral extras (`acme-backend-2`) are created for
  parallelism and deleted after they merge.
- **Labels:** `acme.team/role=<role>` and `acme.team/tier=standing|ephemeral`.
- **Check:** `verify.sh topology` asserts every live `acme-*` agent descends
  from `acme-lead`, with at least 11 present. The parser is unit-tested.

## Messaging and identities

- **Messaging the product owner:** agents use
  `scion message --non-interactive @<ADMIN_EMAIL> "…"`. Messages between agents
  use `scion message <agent>`.
- **One GitHub identity:** every agent acts as the PAT owner on GitHub, which
  has two consequences:
  - A PR can't be *formally* approved by another agent. Reviews are
    `--comment` reviews whose first line is `VERDICT: APPROVE` or
    `VERDICT: REQUEST CHANGES`.
  - Branch protection needs GitHub Pro on private repos. Without it, "no
    direct push to main" is a team rule, not something GitHub enforces. A
    GitHub App per role would fix both.
