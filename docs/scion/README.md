# Scion Autonomous Software Factory — Configuration Guide

This directory holds the **focused Scion setup guides** and the **12 role templates + shared operating protocol** ([`docs/scion/templates/`](./templates/)) modeled after [`duboc/scion-experiment`](https://github.com/duboc/scion-experiment), running two Scion projects on one self-hosted Scion Hub VM:
- **Build Project (`duboc-acme-airlines` on `main`):** `lead`, `researcher`, `product-manager`, `backend`, `frontend`, `reviewer`, `deployer`
- **Eval Project (`acme-airlines-eval` on `eval-dashboard`):** `eval-lead`, `factory-analyst`, `search-evaluator`, `ui-craftsman`, `eval-deployer`

---

## 1. The Files You Configure

| Path | Purpose | Key Settings |
|---|---|---|
| [`infra/factory.env`](../../infra/factory.env) | Public repository, dual-project, version & schedule defaults (tracked) | `BUILD_PROJECT=duboc-acme-airlines`, `EVAL_PROJECT=acme-airlines-eval`, `HEARTBEAT_CRON="*/15 * * * *"`, `BUDGET_USD=2000` |
| `infra/factory.local.env` | Sensitive org, billing & IAP domain overrides (gitignored; backed up in Secret Manager `acme-factory-local-env`) | `FOLDER_ID`, `BILLING_ACCOUNT`, `ORG_DOMAIN`, `WEB_DOMAINS`, `ADMIN_EMAIL` |
| [`infra/deploy-config.json`](../../infra/deploy-config.json) | Scion single-node Hub + Broker VM spec | `project_id`, `region`, `machine_size` (`medium` → `n2-standard-16`), `release_channel` (`nightly`) |
| [`docs/scion/templates/`](./templates/) | Shared protocol (`_shared/agents.md`) + 12 role templates rendered by [`infra/render-templates.py`](../../infra/render-templates.py) | Build: `lead`, `researcher`, `product-manager`, `backend`, `frontend`, `reviewer`, `deployer`; Eval: `eval-lead`, `factory-analyst`, `search-evaluator`, `ui-craftsman`, `eval-deployer` |
| [`AGENTS.md`](../../AGENTS.md) / [`CLAUDE.md`](../../CLAUDE.md) | Repository instructions read by every agent | Build/test commands, "Two Goals, One Guardrail", 6-step GitHub issue protocol |

---

## 2. Recreate & Verify via the `scion` CLI in 3 Commands

```bash
# 1. Authenticate operator gcloud profile & GitHub CLI
gcloud auth login --configuration=acme-factory
gh auth status

# 2. Render & import the 12 templates, provision both projects (build + eval), start the 12 agents, and install both 15-min heartbeats
bash infra/setup-scion.sh

# 3. Verify all 21 infrastructure + 5 end-to-end checks
bash infra/verify.sh all && bash infra/verify.sh e2e
```

---

## 3. Documentation & Templates Index (`docs/scion/`)

| Path | What It Covers |
|---|---|
| **[`01-scion-and-gcp-setup.md`](./01-scion-and-gcp-setup.md)** | Recreating both Hub projects (`duboc-acme-airlines` & `acme-airlines-eval`), the 12 templates, the 12 standing agents, and the two 15-min heartbeats using [`infra/setup-scion.sh`](../../infra/setup-scion.sh) and [`infra/render-templates.py`](../../infra/render-templates.py), plus the **4-step IAP domain setup** (`WEB_DOMAINS`). |
| **[`02-agents-and-heartbeat.md`](./02-agents-and-heartbeat.md)** | The **12 role templates** in [`docs/scion/templates/`](./templates/), the shared operating protocol (`_shared/agents.md`), the **15-minute `HEARTBEAT`** (`*/15 * * * *`), and the **6-step GitHub issue workflow** (`Triage -> Research -> User Story -> Parallel Build/Review/Deploy -> Independent Eval Approval & Close`). |
| **[`03-verification-and-eval.md`](./03-verification-and-eval.md)** | Infrastructure verification (`infra/verify.sh all` / `e2e`) and the **5-layer product evaluation methodology** (in-process deterministic evals, 11-field `STRUCTURAL` parity, and live Vertex AI Agent Engine + Cloud Trace proof). |
| **[`templates/`](./templates/)** | `_shared/agents.md` + the 12 Scion role templates (`scion-agent.yaml`, `system-prompt.md`, `agents.md`, and `skills/impeccable`). |
