# Scion software factory: playbook

How to build and run a **self-driving software factory** on Google Cloud with
[Scion](https://github.com/GoogleCloudPlatform/scion): a team of LLM agents that
keeps a GitHub backlog moving, from issue to merged PR to deployment. It runs
unattended, within a budget, and asks a human only for decisions and new
backlog.

Everything here was learned by building the `acme-airlines` factory (project
`acme-airlines-factory`). The scripts in [`infra/`](../../infra/) are
parameterized by [`infra/factory.env`](../../infra/factory.env), so the same
system can be stood up for another repository or organization.

## Quick start (new factory)

```bash
cp docs/scion/scripts/factory.env.example infra/factory.env   # then edit the values
$EDITOR infra/deploy-config.json                              # project_id, region, hub_name, admin_email
docs/scion/scripts/bootstrap.sh                               # runs every step with a check after each
```

`bootstrap.sh` stops at each **manual step** (OAuth consent and client,
Model Garden, GitHub token, billing export) and tells you exactly what to
click. The full sequence, with the reasoning behind it, is in
[02-runbook-from-zero.md](02-runbook-from-zero.md). Before you start, read the
[new-factory checklist](08-new-factory-checklist.md).

## What you get

| Piece | What it is |
|---|---|
| **Hub VM** | Scion Hub + Runtime Broker on one GCE VM (no public IP), reached through a Cloud Run **IAP** proxy |
| **Agent team** | 13 role templates (lead, eng-manager, architect, backend, frontend, qa, qa-tester, reviewer, security-auditor, investigator, researcher, doc-writer, release-notes): Opus plans, Sonnet codes, Gemini Flash does the rest |
| **Standing roster** | One persistent agent per role under the lead, visible as a tree in the hub's lineage graph |
| **Autonomous loop** | 5-minute wake-on-change watchdog (`acme-factory-watchdog`) plus a 6-hour fallback tick and worker events. The lead triages, dispatches, merges (reviewer verdict + green CI) and reports on a pinned status issue; the watchdog recovers stalled/errored states and reincarnates the lead daily |
| **Backlog policy** | When the backlog is empty, the lead proposes ≤10 `status:proposed` issues and waits for human approval |
| **Cost & security controls** | Dedicated `acme-agent` SA (`assign` mode), per-role turn + model-call caps, concurrency cap, `pause`/`resume`, and a $2,000/month budget guard that **suspends agents** at 100% (it never stops the VM) |
| **Observability** | `infra/verify.sh` (21 platform checks + `e2e`), Scion Cloud Monitoring telemetry export, the Observatory dashboard, PO email alerts, and Agent Engine traces in Cloud Trace |

## Contents

1. [Architecture](01-architecture.md): components, trust boundaries, data flows
2. [Runbook from zero](02-runbook-from-zero.md): every step, in order, with checks
3. [The agent team](03-agent-team.md): templates, models, topology, how to change them
4. [Operating model](04-operating-model.md): the loop, Issues as the work bus, merge gate, backlog
5. [Cost and safety](05-cost-and-safety.md): caps, budget guard, pause, credentials, access
6. [Observability and verification](06-observability.md): `verify.sh`, the Observatory, traces
7. [Lessons learned](07-lessons-learned.md): every gotcha we hit, with root cause and fix
8. [New-factory checklist](08-new-factory-checklist.md)
9. [IAP: user access to the web UIs](09-iap.md): access layers, the custom OAuth client, troubleshooting
10. [Evaluation methodology](10-eval-methodology.md): 5-layer governance suite, structural parity, live Agent Engine proof, and factory evaluation
11. [Scripts](scripts/README.md): `bootstrap.sh`, `factory.env.example`, and the infra script map

## Related records in this repo

- Decisions: [`docs/decisions/`](../decisions/) (ADRs 0001–0015 are the factory; 0100+ are the app)
- Specs: [`docs/superpowers/specs/`](../superpowers/specs/) (the original design plus addenda A–D)
- Day-to-day operations for *this* factory: [`docs/setup/`](../setup/)
