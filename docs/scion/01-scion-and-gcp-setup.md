# 01 — Scion CLI Configuration, GCP Provisioning & IAP Setup

How to provision the GCP project, recreate the entire Scion Hub & agent configuration using the native `scion` CLI ([`infra/setup-scion.sh`](../../infra/setup-scion.sh)), and grant IAP web access to organization domains (`zup.com.br`, `google.com`).

---

## 1. Provisioning & Operations Runbook (`infra/*.sh`)

All scripts source [`infra/factory.env`](../../infra/factory.env) via [`infra/lib.sh`](../../infra/lib.sh) and are idempotent:

| Script | What It Configures |
|---|---|
| [`infra/00-bootstrap-project.sh`](../../infra/00-bootstrap-project.sh) | Creates GCP project (`acme-airlines-factory`), links billing, enables APIs (`compute`, `run`, `iap`, `aiplatform`, `secretmanager`, `monitoring`, `cloudscheduler`), ensures Cloud NAT on `default` VPC. |
| [`infra/10-deploy-hub.sh`](../../infra/10-deploy-hub.sh) | Runs upstream `single-node-vm/deploy.sh --config infra/deploy-config.json` (provisions `scion-hub-acme` VM + `scion-hub-proxy-acme` Cloud Run IAP proxy). |
| **[`infra/setup-scion.sh`](../../infra/setup-scion.sh)** | **Single `scion` CLI setup script:** upgrades `scion-antigravity:latest` (`agy 1.3.4`), syncs `model_aliases` (`scion harness-config sync`), imports & syncs [`docs/scion/templates/`](./templates/) (`scion templates import` + `sync`), sets Hub env vars & project secrets (`scion hub env set` / `scion hub secret set`), verifies `acme-agent@` SA (`scion service-accounts`), and ensures `acme-lead` (`--type coordinator`) + `acme-factory-tick` are running. |
| [`infra/25-agent-identity.sh`](../../infra/25-agent-identity.sh) | Creates low-privilege `acme-agent@` SA, switches Hub to `default_gcp_identity_mode: assign`, and drops `roles/iam.serviceAccountAdmin` from the Hub VM SA. |
| [`infra/30-hub-admin-access.sh`](../../infra/30-hub-admin-access.sh) | `enable` / `disable` / `run`: temporarily opens operator-SA OAuth federation on `localhost:8080` over IAP-tunneled SSH for non-interactive CLI mutations. |
| [`infra/35-github-repo.sh`](../../infra/35-github-repo.sh) | Creates GitHub workflow labels, pinned `🏭 Factory status` issue, and `main` branch protection (`backend`, `engine-smoke`, `frontend`, `pr-size`). |
| [`infra/50-web-access.sh`](../../infra/50-web-access.sh) / [`55-iap-oauth-client.sh`](../../infra/55-iap-oauth-client.sh) | Configures IAP OAuth client + Hub domain access for all `WEB_DOMAINS` (`google.com`, `duboc.altostrat.com`, `zup.com.br`). |
| [`infra/60-factory.sh`](../../infra/60-factory.sh) | `status` / `tick-install` / `pause` / `resume`: controls the `acme-factory-tick` schedule and `acme-lead` coordinator. |
| [`infra/70-budget-guard.sh`](../../infra/70-budget-guard.sh) | Deploys the `$2,000` billing budget + Pub/Sub + `acme-budget-guard` Cloud Run auto-suspend function. |
| [`infra/75-backups.sh`](../../infra/75-backups.sh) / [`76-watchdog.sh`](../../infra/76-watchdog.sh) | Attaches daily persistent-disk snapshot schedule and deploys `acme-factory-watchdog` (Cloud Run + **hourly** `0 * * * *` Cloud Scheduler + `04:00 UTC` daily reincarnation + Cloud Monitoring email alert). |
| [`infra/80-observatory-platform.sh`](../../infra/80-observatory-platform.sh) / [`90-irrops-demo-platform.sh`](../../infra/90-irrops-demo-platform.sh) / [`95-agent-engine-platform.sh`](../../infra/95-agent-engine-platform.sh) | Deploys product Cloud Run services (`acme-observatory-api`, `acme-observatory-web`, `acme-irrops-demo`), GitHub WIF, and the two Vertex AI Reasoning Engines. |

---

## 2. Recreating Configuration & Agents with the `scion` CLI (`infra/setup-scion.sh`)

All Hub and agent configuration is recreated declaratively via the `scion` CLI inside a temporary `30-hub-admin-access.sh` session (`scion --hub http://localhost:8080 ...`):

### Step 1: Sync Harness `model_aliases` (`scion harness-config sync`)
Templates in [`docs/scion/templates/*/scion-agent.yaml`](./templates/) use abstract tier names (`model: large` or `model: medium`). [`infra/setup-scion.sh`](../../infra/setup-scion.sh) writes `model_aliases` into `/home/scion/.scion/harness-configs/<name>/config.yaml` and publishes each harness config to the Hub via the CLI:
```bash
scion --hub http://localhost:8080 --global harness-config sync claude
scion --hub http://localhost:8080 --global harness-config sync antigravity
scion --hub http://localhost:8080 --global harness-config sync gemini-cli
```
- **`claude` (Claude Code):** `large: "claude-opus-5-5"`, `medium: "claude-sonnet-5-5"`, `small: "claude-haiku-5-5"`
- **`antigravity` (`agy 1.3.4`):** `large: "Gemini 4 Argon (High)"` (`gemini-4-argon`), `medium: "Gemini 3.8 Flash (Medium)"` (`gemini-3.8-flash`)
- **`gemini-cli`:** `large: "gemini-4-argon"`, `medium: "gemini-3.8-flash"`

### Step 2: Import & Sync the 6 Templates from `docs/scion/templates/` (`scion templates import` + `sync`)
`scion templates import` natively discovers every Scion template subdirectory (`scion-agent.yaml`, `agents.md`, `system-prompt.md`, `skills/`) inside [`docs/scion/templates/`](./templates/) and copies them into `~/.scion/templates/`, and `scion templates sync --all` uploads all 6 to the Hub:
```bash
scion --hub http://localhost:8080 --global templates import --all --force /path/to/docs/scion/templates
scion --hub http://localhost:8080 --global templates sync --all
```

### Step 3: Configure Project, Shared Scratchpad, Env Vars & Secrets (`scion hub` / `scion shared-dir`)
> **CLI flag note (`--project=<slug>`):** In `scion hub secret` and `scion hub env`, `--project` has a Cobra `NoOptDefVal` so bare `--project` infers the current directory's project. When passing an explicit project slug or ID, always use `=` (`--project=duboc-acme-airlines`).

```bash
# Project & shared /scion-volumes/scratchpad directory
scion --hub http://localhost:8080 hub project create https://github.com/duboc/acme-airlines.git
scion --hub http://localhost:8080 --project duboc-acme-airlines shared-dir create scratchpad

# Hub-wide environment variables (Vertex AI global endpoint)
scion --hub http://localhost:8080 hub env set --scope=hub --always GOOGLE_CLOUD_PROJECT=acme-airlines-factory
scion --hub http://localhost:8080 hub env set --scope=hub --always GOOGLE_CLOUD_LOCATION=global

# Project-scoped secret
scion --hub http://localhost:8080 hub secret set --project=duboc-acme-airlines GITHUB_TOKEN "$GITHUB_PAT"
```

### Step 4: Register & Verify Least-Privilege Agent Service Account (`scion service-accounts`)
```bash
scion --hub http://localhost:8080 --global service-accounts add \
  acme-agent@acme-airlines-factory.iam.gserviceaccount.com \
  --gcp-project acme-airlines-factory --name "ACME Agent SA"
scion --hub http://localhost:8080 --global service-accounts verify <sa-id>
```

### Step 5: Start Persistent Coordinator (`acme-lead`) & Schedule (`scion start` / `scion schedule`)
```bash
scion --hub http://localhost:8080 --project duboc-acme-airlines start acme-lead \
  --type coordinator --role full \
  "You are acme-lead, the persistent coordinator for duboc/acme-airlines. Read AGENTS.md and run a factory loop pass."

scion --hub http://localhost:8080 --project duboc-acme-airlines schedule create-recurring \
  --name acme-factory-tick --cron "0 */6 * * *" \
  --type message --agent acme-lead --message "TICK: run one factory loop pass..."
```

---

## 3. Adding or Updating an IAP Domain (e.g., `zup.com.br`)

Granting browser sign-in to an organization domain requires **4 coordinated layers**:

| Step | Layer | Why It Is Required |
|---|---|---|
| **1** | **Google Workspace Primary/Verified Domain** | Every user's Google account email must belong to the Google Workspace domain (`hd` claim in the IAP JWT matches `zup.com.br`). |
| **2** | **GCP IAP IAM (`roles/iap.httpsResourceAccessor`)** | Must be bound on **all 3 Cloud Run services**: `scion-hub-proxy-acme`, `acme-observatory-web`, and `acme-observatory-api` for `domain:<domain>`. |
| **3** | **Scion Hub `authorized_domains` + `hub.db` (`origin='seeded'`)** | Scion persists `hub_settings` key `access` in `/home/scion/.scion/hub.db` and only refreshes rows from `settings.yaml` when `origin = 'seeded'`. [`infra/50-web-access.sh`](../../infra/50-web-access.sh) updates both `settings.yaml` and `hub.db` (`origin='seeded'`) and restarts `scion-hub`. |
| **4** | **OAuth Brand Audience (`Internal` vs `External`)** | If the OAuth consent screen in `acme-airlines-factory` (`duboc.altostrat.com`) is set to **Internal**, Google OAuth rejects users from external Google Workspace orgs (`org_internal`) before IAP runs. Switch **APIs & Services → OAuth consent screen → User Type** to **External (In production)**; IAP IAM + Hub `authorized_domains` still block all public Gmail users. |

```bash
# Add domain to WEB_DOMAINS in infra/factory.env, then apply and verify:
bash infra/50-web-access.sh && bash infra/verify.sh webaccess
```
