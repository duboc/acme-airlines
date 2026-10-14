# Scion Autonomous Software Factory — Configuration Guide

This directory holds the **focused Scion setup guides** and the **6 [`scion-frontiers/agent-team`](https://github.com/scion-frontiers/agent-team) role templates** ([`docs/scion/templates/`](./templates/)) used to run the autonomous AI software factory on Google Cloud + [Scion](https://github.com/GoogleCloudPlatform/scion).

---

## 1. The Files You Configure

| Path | Purpose | Key Settings |
|---|---|---|
| [`infra/factory.env`](../../infra/factory.env) | GCP, GitHub, IAP & cost guardrails | `PROJECT_ID`, `BILLING_ACCOUNT`, `GITHUB_REPO`, `WEB_DOMAINS`, `MONTHLY_BUDGET_USD=2000` |
| [`infra/deploy-config.json`](../../infra/deploy-config.json) | Scion single-node Hub + Broker VM spec | `machineType` (`n2-standard-16`), `githubAppWebhooksEnabled`, `adminEmails`, `releaseChannel` (`nightly-20261009`) |
| [`docs/scion/templates/`](./templates/) | The 6 agent role templates read by `scion templates import` | `coordinator`, `eng-manager`, `architect`, `developer`, `code-reviewer`, `test-engineer` |
| [`AGENTS.md`](../../AGENTS.md) / [`CLAUDE.md`](../../CLAUDE.md) | Repository instructions read by every agent | Build/test commands (`uv run pytest`, `npm test`), product guardrails, `/scion-volumes/scratchpad/` conventions |

---

## 2. Recreate & Verify via the `scion` CLI in 3 Commands

```bash
# 1. Authenticate operator gcloud profile & GitHub CLI
gcloud auth login --configuration=acme-factory
gh auth status

# 2. Sync harness model_aliases, import & push docs/scion/templates/, configure env/SA, and start acme-lead
bash infra/setup-scion.sh

# 3. Verify all 21 infrastructure + 5 end-to-end checks
bash infra/verify.sh all && bash infra/verify.sh e2e
```

---

## 3. Documentation & Templates Index (`docs/scion/`)

| Path | What It Covers |
|---|---|
| **[`01-scion-and-gcp-setup.md`](./01-scion-and-gcp-setup.md)** | Recreating the Hub & agents using the native `scion` CLI ([`infra/setup-scion.sh`](../../infra/setup-scion.sh): `templates import`, `templates sync`, `harness-config sync`, `hub secret set --project=...`, `hub env set`, `service-accounts add/verify`, `start`) and the **4-step IAP domain setup** (`zup.com.br`, `google.com`). |
| **[`02-agents-and-heartbeat.md`](./02-agents-and-heartbeat.md)** | The **6-template core** in [`docs/scion/templates/`](./templates/) (`coordinator`, `eng-manager`, `architect`, `developer`, `code-reviewer`, `test-engineer`), model mapping (`claude-opus-5-5`, `claude-sonnet-5-5`, `gemini-4-argon`, `gemini-3.8-flash`), ephemeral worker lifecycle, and the **4-layer heartbeat** (**hourly `0 * * * *`** watchdog, daily `04:00 UTC` reincarnation, 6h safety net, `$2,000` budget kill-switch). |
| **[`03-verification-and-eval.md`](./03-verification-and-eval.md)** | Infrastructure verification (`infra/verify.sh all` / `e2e`) and the **5-layer product evaluation methodology** (in-process deterministic evals, 11-field `STRUCTURAL` parity, and live Vertex AI Agent Engine + Cloud Trace proof). |
| **[`templates/`](./templates/)** | The 6 Scion templates (`scion-agent.yaml`, `agents.md`, `system-prompt.md`, and `developer/skills/impeccable`) imported directly by `scion --global templates import --all --force`. |
