# Scion Autonomous Software Factory — Configuration Guide

This directory holds the **focused Scion setup guides** and the **14 role templates + shared operating protocol** ([`docs/scion/templates/`](./templates/)) modeled after [`duboc/scion-experiment`](https://github.com/duboc/scion-experiment), running two Scion projects on one self-hosted Scion Hub VM:
- **Build Project (`duboc-acme-airlines` on `main`):** `lead`, `heartbeat`, `researcher`, `product-manager`, `backend`, `frontend`, `reviewer`, `deployer`
- **Eval Project (`acme-airlines-eval` on `eval-dashboard`):** `eval-lead`, `eval-heartbeat`, `factory-analyst`, `search-evaluator`, `ui-craftsman`, `eval-deployer`

---

## 1. The Files You Configure

| Path | Purpose | Key Settings |
|---|---|---|
| [`infra/factory.env`](../../infra/factory.env) | Public repository, dual-project, version & schedule defaults (tracked) | `BUILD_PROJECT=duboc-acme-airlines`, `EVAL_PROJECT=acme-airlines-eval`, `HEARTBEAT_CRON="0 12 * * *"`, `WATCHDOG_CRON="0 12 * * *"`, `BUDGET_USD=2000` |
| `infra/factory.local.env` | Sensitive org, billing & IAP domain overrides (gitignored; backed up in Secret Manager `acme-factory-local-env`) | `FOLDER_ID`, `BILLING_ACCOUNT`, `ORG_DOMAIN`, `WEB_DOMAINS`, `ADMIN_EMAIL` |
| [`infra/deploy-config.json`](../../infra/deploy-config.json) | Scion single-node Hub + Broker VM spec | `project_id`, `region`, `machine_size` (`medium` → `n2-standard-16`), `release_channel` (`nightly`) |
| [`docs/scion/templates/`](./templates/) | Shared protocol (`_shared/agents.md`) + 14 role templates rendered by [`infra/render-templates.py`](../../infra/render-templates.py) | Build: `lead`, `heartbeat`, `researcher`, `product-manager`, `backend`, `frontend`, `reviewer`, `deployer`; Eval: `eval-lead`, `eval-heartbeat`, `factory-analyst`, `search-evaluator`, `ui-craftsman`, `eval-deployer` |
| [`AGENTS.md`](../../AGENTS.md) / [`CLAUDE.md`](../../CLAUDE.md) | Repository instructions read by every agent | Build/test commands, "Two Goals, One Guardrail", 6-step GitHub issue protocol |

---

## 2. Recreate & Verify via the `scion` CLI in 3 Commands

```bash
# 1. Authenticate operator gcloud profile & GitHub CLI
gcloud auth login --configuration=acme-factory
gh auth status

# 2. Render & import the 14 templates, provision both projects (build + eval), start the 14 agents, and install both daily heartbeats
bash infra/setup-scion.sh

# 3. Verify all 21 infrastructure + 5 end-to-end checks
bash infra/verify.sh all && bash infra/verify.sh e2e
```

---

## 3. Documentation & Templates Index (`docs/scion/`)

| Path | What It Covers |
|---|---|
| **[`01-scion-and-gcp-setup.md`](./01-scion-and-gcp-setup.md)** | Recreating both Hub projects (`duboc-acme-airlines` & `acme-airlines-eval`), the 14 templates, the 14 standing agents, the two once-per-day heartbeats (`0 12 * * *`), and Hub-scoped Claude context/Haiku cost guardrails using [`infra/setup-scion.sh`](../../infra/setup-scion.sh) and [`infra/render-templates.py`](../../infra/render-templates.py), plus the **4-step IAP domain setup** (`WEB_DOMAINS`). |
| **[`02-agents-and-heartbeat.md`](./02-agents-and-heartbeat.md)** | The **14 role templates** in [`docs/scion/templates/`](./templates/), the shared operating protocol (`_shared/agents.md`), the **dedicated daily `heartbeat` / `eval-heartbeat` agents** (`0 12 * * *`), the **6-step GitHub issue workflow**, and the **4 cost-control guardrails** (daily Flash heartbeat sensor, `<200k` context window caps, `claude-haiku-5-5` model pinning, and daily/proactive lead reincarnation). |
| **[`03-verification-and-eval.md`](./03-verification-and-eval.md)** | Infrastructure verification (`infra/verify.sh all` / `e2e`) and the **4-boundary product evaluation methodology** (Gemini intent & fail-closed safety, fact-grounded composition, deterministic policy/A2A guardrails, and 5-preset `STRUCTURAL` parity + Cloud Trace proof). |
| **[`templates/`](./templates/)** | `_shared/agents.md` + the 14 Scion role templates (`scion-agent.yaml`, `system-prompt.md`, `agents.md`, and `skills/impeccable`). |
