# 01 — Scion CLI Configuration, Two-Project Provisioning & IAP Setup

How to provision the GCP project, manage sensitive organization/domain parameters via `infra/factory.local.env` (and Secret Manager), recreate both Scion Hub projects (`duboc-acme-airlines` for development and `acme-airlines-eval` for evaluation) and all 10 agents using the native `scion` CLI ([`infra/setup-scion.sh`](../../infra/setup-scion.sh)), and grant IAP web access to organization domains (`WEB_DOMAINS`).

---

## 1. Provisioning & Operations Runbook (`infra/*.sh`)

All scripts source public defaults from [`infra/factory.env`](../../infra/factory.env) and sensitive organization/domain parameters (`FOLDER_ID`, `BILLING_ACCOUNT`, `ORG_DOMAIN`, `WEB_DOMAINS`, `ADMIN_EMAIL`) from gitignored `infra/factory.local.env` via [`infra/lib.sh`](../../infra/lib.sh). If `infra/factory.local.env` is absent on a fresh workstation, [`infra/lib.sh`](../../infra/lib.sh) automatically fetches it from GCP Secret Manager (`acme-factory-local-env`).

| Script | What It Configures |
|---|---|
| [`infra/00-bootstrap-project.sh`](../../infra/00-bootstrap-project.sh) | Creates GCP project (`acme-airlines-factory`), links billing, enables APIs (`compute`, `run`, `iap`, `aiplatform`, `secretmanager`, `monitoring`, `cloudscheduler`), ensures Cloud NAT on `default` VPC. |
| [`infra/10-deploy-hub.sh`](../../infra/10-deploy-hub.sh) | Runs upstream `single-node-vm/deploy.sh --config <runtime-config.json>` (injects `$ADMIN_EMAIL` at deploy time and provisions `scion-hub-acme` VM + `scion-hub-acme-iap-proxy` Cloud Run IAP proxy). |
| **[`infra/setup-scion.sh`](../../infra/setup-scion.sh)** | **Declarative `scion` CLI setup script:** upgrades `scion-antigravity:latest` (`agy 1.3.4`), syncs `model_aliases` (`scion harness-config sync`), renders & imports the 10 templates from [`docs/scion/templates/`](./templates/) via [`infra/render-templates.py`](../../infra/render-templates.py) (`scion templates import` + `sync`), provisions both Hub projects (`duboc-acme-airlines` on `main` & `acme-airlines-eval` on orphan branch `eval-dashboard`), sets `GITHUB_TOKEN` (`injection_mode=always`), verifies `acme-agent@` and `acme-observatory-deployer@` SAs, starts all 10 standing agents, and installs both 15-minute heartbeats. |
| [`infra/25-agent-identity.sh`](../../infra/25-agent-identity.sh) | Creates low-privilege `acme-agent@` SA, switches Hub to `default_gcp_identity_mode: assign`, and drops `roles/iam.serviceAccountAdmin` from the Hub VM SA. |
| [`infra/30-hub-admin-access.sh`](../../infra/30-hub-admin-access.sh) | `enable` / `disable` / `status`: temporarily opens operator OAuth federation on `localhost:8080` over IAP-tunneled SSH for non-interactive CLI mutations. |
| [`infra/35-github-repo.sh`](../../infra/35-github-repo.sh) | Creates project labels (`duboc-acme-airlines`, `acme-airlines-eval`), wave labels (`wave-1`..`wave-5`), pinned `🏭 Factory status` issue, orphan branch `eval-dashboard`, and `main` branch protection. |
| [`infra/50-web-access.sh`](../../infra/50-web-access.sh) / [`55-iap-oauth-client.sh`](../../infra/55-iap-oauth-client.sh) | Configures IAP OAuth client + Hub domain access for all `WEB_DOMAINS` in `infra/factory.local.env`. |
| [`infra/60-factory.sh`](../../infra/60-factory.sh) | `status` / `tick-install` / `pause` / `resume`: controls the two 15-minute heartbeat schedules (`duboc-acme-airlines-lead-heartbeat` & `acme-airlines-eval-lead-heartbeat`) and all 10 agents across both projects. |
| [`infra/70-budget-guard.sh`](../../infra/70-budget-guard.sh) | Deploys the `$2,000` billing budget + Pub/Sub + `acme-budget-guard` Cloud Run auto-suspend function. |
| [`infra/75-backups.sh`](../../infra/75-backups.sh) / [`76-watchdog.sh`](../../infra/76-watchdog.sh) | Attaches daily persistent-disk snapshot schedule and deploys `acme-factory-watchdog` (hourly self-healing check + `04:00 UTC` daily reincarnation + Cloud Monitoring email alert). |
| [`infra/80-observatory-platform.sh`](../../infra/80-observatory-platform.sh) / [`90-irrops-demo-platform.sh`](../../infra/90-irrops-demo-platform.sh) / [`95-agent-engine-platform.sh`](../../infra/95-agent-engine-platform.sh) | Deploys product & eval Cloud Run services (`acme-observatory`, `acme-observatory-collector`, `acme-irrops-demo`), GitHub WIF, and the two Vertex AI Reasoning Engines. |

---

## 2. Recreating Both Projects & the 10 Agents with the `scion` CLI (`infra/setup-scion.sh`)

### Step 1: Sync Harness `model_aliases` (`scion harness-config sync`)
```bash
scion --global harness-config sync claude
scion --global harness-config sync antigravity
scion --global harness-config sync gemini-cli
```
- **`claude` (Claude Code):** `large: "claude-opus-5-5"`, `medium: "claude-sonnet-5-5"`, `small: "claude-haiku-5-5"`
- **`antigravity` (`agy 1.3.4`):** `large: "Gemini 4 Argon (High)"` (`gemini-4-argon`), `medium: "Gemini 3.8 Flash (Medium)"` (`gemini-3.8-flash`)

### Step 2: Render, Import & Sync the 10 Templates (`infra/render-templates.py` + `scion templates`)
[`infra/render-templates.py`](../../infra/render-templates.py) combines [`docs/scion/templates/_shared/agents.md`](./templates/_shared/agents.md) into each role's `agents.md` and substitutes `${VAR}` placeholders from `infra/factory.env` + `infra/factory.local.env`:
```bash
python3 infra/render-templates.py --out /tmp/scion-templates
scion --global templates import --all --force /tmp/scion-templates
scion --global templates sync --all
```

### Step 3: Configure the Two Projects (`duboc-acme-airlines` & `acme-airlines-eval`), Secrets & Service Accounts
- **Build project (`duboc-acme-airlines`):** `/home/scion/.scion/projects/duboc-acme-airlines` checked out on `BUILD_BRANCH=main`.
- **Eval project (`acme-airlines-eval`):** `/home/scion/.scion/projects/acme-airlines-eval` checked out on independent orphan trunk `EVAL_BRANCH=eval-dashboard`.
- Both projects receive `GITHUB_TOKEN` with `injection_mode=always`, default SA `acme-agent@acme-airlines-factory.iam.gserviceaccount.com` (`assign` mode), and deployer SA `acme-observatory-deployer@acme-airlines-factory.iam.gserviceaccount.com` assigned to `deployer` and `eval-deployer`.

### Step 4: Start the 10 Standing Agents & Two 15-Minute Heartbeats (`scion start` / `scion schedule`)
```bash
# Build project (duboc-acme-airlines on main)
scion --project duboc-acme-airlines start lead --type lead --role full "Read your instructions, then run your wake-up steps."
scion --project duboc-acme-airlines start backend --type backend "Stand by for briefs from lead."
scion --project duboc-acme-airlines start frontend --type frontend "Stand by for briefs from lead."
scion --project duboc-acme-airlines start reviewer --type reviewer "Stand by for review requests from lead."
scion --project duboc-acme-airlines start deployer --type deployer "Stand by for deploy requests from lead."

# Eval project (acme-airlines-eval on eval-dashboard)
scion --project acme-airlines-eval start eval-lead --type eval-lead --role full "Read your instructions, then run your wake-up steps."
scion --project acme-airlines-eval start factory-analyst --type factory-analyst "Stand by for briefs from eval-lead."
scion --project acme-airlines-eval start search-evaluator --type search-evaluator "Stand by for briefs from eval-lead."
scion --project acme-airlines-eval start ui-craftsman --type ui-craftsman "Stand by for briefs from eval-lead."
scion --project acme-airlines-eval start eval-deployer --type eval-deployer "Stand by for deploy requests from eval-lead."

# Install the two 15-minute heartbeat schedules (*/15 * * * *)
bash infra/60-factory.sh tick-install
```

---

## 3. Adding or Updating an IAP Domain (`WEB_DOMAINS`)

Granting browser sign-in to an organization domain (e.g., `partner.example.com`) requires **4 coordinated layers**:

| Step | Layer | Why It Is Required |
|---|---|---|
| **1** | **Google Workspace Primary/Verified Domain** | Every user's Google account email must belong to the Google Workspace domain (`hd` claim in the IAP JWT matches `<domain>`). |
| **2** | **GCP IAP IAM (`roles/iap.httpsResourceAccessor`)** | Must be bound on **all 3 Cloud Run services**: `scion-hub-acme-iap-proxy`, `acme-observatory`, and `acme-irrops-demo` for `domain:<domain>`. |
| **3** | **Scion Hub `authorized_domains` + `hub.db` (`origin='seeded'`)** | Scion persists `hub_settings` key `access` in `/home/scion/.scion/hub.db` and only refreshes rows from `settings.yaml` when `origin = 'seeded'`. [`infra/50-web-access.sh`](../../infra/50-web-access.sh) updates both `settings.yaml` and `hub.db` (`origin='seeded'`) and restarts `scion-hub`. |
| **4** | **OAuth Brand Audience (`Internal` vs `External`)** | If the OAuth consent screen in `acme-airlines-factory` (`ORG_DOMAIN`) is set to **Internal**, Google OAuth rejects users from external Google Workspace orgs (`org_internal`) before IAP runs. Switch **APIs & Services → OAuth consent screen → User Type** to **External (In production)**; IAP IAM + Hub `authorized_domains` still block all public Gmail users. |

```bash
# 1. Update WEB_DOMAINS in infra/factory.local.env and sync to Secret Manager:
gcloud secrets versions add acme-factory-local-env --project=acme-airlines-factory --data-file=infra/factory.local.env

# 2. Apply and verify:
bash infra/50-web-access.sh && bash infra/verify.sh webaccess
```
