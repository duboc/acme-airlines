# Scion Autonomous Software Factory — Configuration Guide

This directory documents **only what you need to configure** to run an autonomous AI software factory on Google Cloud + [Scion](https://github.com/GoogleCloudPlatform/scion) + [`scion-frontiers/agent-team`](https://github.com/scion-frontiers/agent-team).

---

## 1. The 3 Files You Configure

| File | Purpose | Key Settings |
|---|---|---|
| [`infra/factory.env`](../../infra/factory.env) | GCP, GitHub, IAP & cost guardrails | `PROJECT_ID`, `BILLING_ACCOUNT`, `GITHUB_REPO`, `WEB_DOMAINS`, `MONTHLY_BUDGET_USD=2000` |
| [`infra/deploy-config.json`](../../infra/deploy-config.json) | Scion single-node Hub + Broker VM spec | `machineType` (`n2-standard-16`), `githubAppWebhooksEnabled`, `adminEmails`, `releaseChannel` (`nightly-20261009`) |
| [`AGENTS.md`](../../AGENTS.md) / [`CLAUDE.md`](../../CLAUDE.md) | Repository instructions read by every agent | Build/test commands (`uv run pytest`, `npm test`), product guardrails, `/scion-volumes/scratchpad/` conventions |

---

## 2. Bootstrap & Verify in 3 Commands

```bash
# 1. Authenticate operator gcloud profile & GitHub CLI
gcloud auth login --configuration=acme-factory
gh auth status

# 2. Run the idempotent bootstrap orchestrator (runs infra/00..95 in order)
bash docs/scion/scripts/bootstrap.sh

# 3. Verify all 21 infrastructure + 5 end-to-end checks
bash infra/verify.sh all && bash infra/verify.sh e2e
```

---

## 3. Focused Documentation Index

| Guide | What It Covers |
|---|---|
| **[`01-scion-and-gcp-setup.md`](./01-scion-and-gcp-setup.md)** | The 6 Scion configuration mechanisms (`deploy-config.json`, `settings.yaml` + `hub.db` `origin='seeded'`, temporary `allow_all` CLI federation, REST API `/api/v1/…` for secrets & `acme-agent@` `assign` SA, UATs, `scratchpad` shared dir) and the **4-step IAP domain setup** (`zup.com.br`, `google.com`). |
| **[`02-agents-and-heartbeat.md`](./02-agents-and-heartbeat.md)** | How agent templates are built from [`scion-frontiers/agent-team`](https://github.com/scion-frontiers/agent-team) (`infra/40-build-team-templates.sh`), the 13-role model/cap table, 3-tier lineage hierarchy, and the **4-layer heartbeat** (worker messages, **hourly `0 * * * *`** watchdog, daily `04:00 UTC` reincarnation, 6h safety net, `$2,000` budget kill-switch). |
| **[`03-verification-and-eval.md`](./03-verification-and-eval.md)** | Infrastructure verification (`infra/verify.sh all` / `e2e`) and the **5-layer product evaluation methodology** (in-process deterministic evals, 11-field `STRUCTURAL` parity, and live Vertex AI Agent Engine + Cloud Trace proof). |
| **[`scripts/README.md`](./scripts/README.md)** | Sanitized `factory.env.example`, `deploy-config.example.json`, and `bootstrap.sh` for spinning up a new factory. |
