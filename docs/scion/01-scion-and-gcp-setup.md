# 01 — Scion, GCP & IAP Configuration

Everything needed to provision the GCP project, configure the Scion single-node Hub + Broker VM, wire agent least-privilege IAM, configure harness models, and grant IAP web access to organization domains.

---

## 1. Numbered Provisioning Runbook (`infra/*.sh`)

All scripts source [`infra/factory.env`](../../infra/factory.env) via [`infra/lib.sh`](../../infra/lib.sh) and are idempotent:

| Script | What It Configures |
|---|---|
| [`infra/00-bootstrap-project.sh`](../../infra/00-bootstrap-project.sh) | Creates GCP project (`acme-airlines-factory`), links billing, enables APIs (`compute`, `run`, `iap`, `aiplatform`, `secretmanager`, `monitoring`, `cloudscheduler`), ensures Cloud NAT on `default` VPC. |
| [`infra/10-deploy-hub.sh`](../../infra/10-deploy-hub.sh) | Runs upstream `single-node-vm/deploy.sh --config infra/deploy-config.json` (provisions `scion-hub-acme` VM + `scion-hub-proxy-acme` Cloud Run IAP proxy). |
| [`infra/20-model-access.sh`](../../infra/20-model-access.sh) | Upgrades `scion-antigravity:latest` to `agy 1.3.4` and configures `model_aliases` (`claude-opus-5-5`, `claude-sonnet-5-5`, `gemini-4-argon` / `"Gemini 4 Argon (High)"`, `gemini-3.8-flash` / `"Gemini 3.8 Flash (Medium)"`). |
| [`infra/25-agent-identity.sh`](../../infra/25-agent-identity.sh) | Creates low-privilege `acme-agent@` SA, switches Hub to `default_gcp_identity_mode: assign`, and drops `roles/iam.serviceAccountAdmin` from the Hub VM SA. |
| [`infra/30-hub-admin-access.sh`](../../infra/30-hub-admin-access.sh) | `enable` / `disable` / `run`: temporarily opens operator-SA OAuth federation on `localhost:8080` over IAP-tunneled SSH for non-interactive CLI mutations. |
| [`infra/31-set-project-secret.sh`](../../infra/31-set-project-secret.sh) / [`35-github-repo.sh`](../../infra/35-github-repo.sh) | Seeds project-scoped `GITHUB_TOKEN`, sets `GOOGLE_CLOUD_LOCATION=global`, `VERTEX_LOCATION=us-central1`, `SCION_OTEL_ENABLED=true`, mounts `/scion-volumes/scratchpad`, and registers the GitHub App. |
| [`infra/40-build-team-templates.sh`](../../infra/40-build-team-templates.sh) | Builds the 6 [`scion-frontiers/agent-team`](https://github.com/scion-frontiers/agent-team) templates (`coordinator`, `eng-manager`, `architect`, `developer`, `code-reviewer`, `test-engineer`). |
| [`infra/45-push-templates.sh`](../../infra/45-push-templates.sh) | Pushes the 6 templates to the Hub (`scion templates push`) and prunes any obsolete templates or idle worker agents. |
| [`infra/50-web-access.sh`](../../infra/50-web-access.sh) / [`55-iap-oauth-client.sh`](../../infra/55-iap-oauth-client.sh) | Configures IAP OAuth client + Hub domain access for all `WEB_DOMAINS` (`google.com`, `duboc.altostrat.com`, `zup.com.br`). |
| [`infra/60-factory.sh`](../../infra/60-factory.sh) | `status` / `tick-install` / `pause` / `resume`: controls the `acme-factory-tick` schedule and `acme-lead` coordinator. |
| [`infra/70-budget-guard.sh`](../../infra/70-budget-guard.sh) | Deploys the `$2,000` billing budget + Pub/Sub + `acme-budget-guard` Cloud Run auto-suspend function. |
| [`infra/75-backups.sh`](../../infra/75-backups.sh) / [`76-watchdog.sh`](../../infra/76-watchdog.sh) | Attaches daily persistent-disk snapshot schedule and deploys `acme-factory-watchdog` (Cloud Run + **hourly** `0 * * * *` Cloud Scheduler + `04:00 UTC` daily reincarnation + Cloud Monitoring email alert). |
| [`infra/80-observatory-platform.sh`](../../infra/80-observatory-platform.sh) / [`95-agent-engine-platform.sh`](../../infra/95-agent-engine-platform.sh) | Deploys product Cloud Run services (`acme-observatory-api`, `acme-observatory-web`), GitHub WIF, and the two Vertex AI Reasoning Engines. |

---

## 2. The 6 Scion Configuration Planes (Key Mechanics & Gotchas)

Scion does not use a single configuration file. It has **6 distinct configuration planes**:

### Plane 1: `infra/deploy-config.json` (Provisioning Inputs)
Consumed by upstream `single-node-vm/deploy.sh`. Controls the VM shape (`n2-standard-16`, `200 GB` pd-balanced), container image (`harnesses-full:nightly-20261009`), and `scion:harnesses` (`claude`, `gemini`, `opencode`, `codex`).
- **Gotcha:** Re-running `deploy.sh` re-grants `roles/iam.serviceAccountAdmin` to the Hub VM SA; always run `bash infra/25-agent-identity.sh` afterward to restore `assign` mode.

### Plane 2: `/home/scion/.scion/settings.yaml` + `hub.db` (`origin='seeded'`)
Runtime settings loaded by `scion-hub.service` on the VM (`server.auth`, `server.identity.default_gcp_identity_mode: assign`, `telemetry.enabled: true`, `github_app`).
- **Gotcha (`hub_settings.access` override):** Editing `settings.yaml` alone is **not sufficient** for `user_access_mode` or `authorized_domains` once the Hub has booted. Scion persists `hub_settings` key `access` in `/home/scion/.scion/hub.db`. Only rows with `origin = 'seeded'` are refreshed from `settings.yaml` on restart (`origin = 'api'` overrides YAML forever). Always update both `settings.yaml` and the SQLite `hub_settings` row with `origin='seeded'` (automated by [`infra/50-web-access.sh`](../../infra/50-web-access.sh)).

### Plane 3: Temporary Operator Federation (`infra/30-hub-admin-access.sh`)
In `auth.mode: proxy` (IAP), `scion` CLI commands on the VM (`localhost:8080`) lack an IAP browser JWT. [`infra/30-hub-admin-access.sh`](../../infra/30-hub-admin-access.sh) `run` temporarily patches an `# ACME-ADMIN-FEDERATION-BEGIN..END` block into `settings.yaml` (`federation.enabled: true, mode: allow_all`), mints an admin token via `google-metadata` ADC, runs your CLI command, and removes the block in an `EXIT` trap.

### Plane 4: Hub REST API (`http://localhost:8080/api/v1/…`)
Operations without dedicated CLI flags are performed via `curl` against `http://localhost:8080/api/v1/…` inside a `30-hub-admin-access.sh run` session:
- **Project secrets (`GITHUB_TOKEN`):** `PUT /api/v1/secrets/GITHUB_TOKEN` with `{"value": "...", "scope": "project", "scopeId": "<PROJECT_UUID>", "type": "environment", "target": "GITHUB_TOKEN"}`. (Required because `scion hub secret set` writes user-scoped secrets, not project-scoped secrets.)
- **Agent GCP service account (`assign` mode):**
  1. `POST /api/v1/projects/<PROJECT_UUID>/gcp-service-accounts` with `{"email": "acme-agent@...", "default": true}`
  2. `POST /api/v1/projects/<PROJECT_UUID>/gcp-service-accounts/<SA_ID>/verify`
  3. Patch active agents' `applied_config` in `hub.db` so `"gcp_identity": {"MetadataMode": "assign", "ServiceAccountEmail": "acme-agent@..."}`.
- **Shared workspace directory (`/scion-volumes/scratchpad`):** Configured on the project record (`shared_dirs = [{"name":"scratchpad"}]`) so every agent container mounts `/scion-volumes/scratchpad` for durable briefs and state files.

### Plane 5: Project-Scoped User Access Tokens (`scion_pat_…`)
Automation services (`acme-budget-guard`, `acme-factory-watchdog`) never SSH to the VM. Instead, their setup scripts mint a project-scoped UAT (`scion hub token create --project "$PROJECT_SLUG" --scopes ...`), store it in Secret Manager (`acme-budget-guard-hub-token`, `acme-watchdog-hub-token`), and call `http://<vm-internal-ip>:8080/api/v1/…` directly over Direct VPC egress.

### Plane 6: Harness `model_aliases` & Vertex AI Routing (`infra/20-model-access.sh`)
Templates in `factory/templates/*/scion-agent.yaml` specify abstract tier names (`model: large` or `model: medium`) and remain model-version-agnostic. [`infra/20-model-access.sh`](../../infra/20-model-access.sh) upgrades `scion-antigravity:latest` to `agy 1.3.4` and patches `model_aliases` across `/home/scion/.scion/harness-configs/*/config.yaml`, `/opt/scion-source/harnesses/*/config.yaml`, and `harness_configs` in `hub.db`:
- **`claude` (Claude Code):** `large: "claude-opus-5-5"`, `medium: "claude-sonnet-5-5"`, `small: "claude-haiku-5-5"` (`GOOGLE_CLOUD_LOCATION=global`, `CLOUD_ML_REGION=global`).
- **`antigravity` (`agy 1.3.4`):** `large: "Gemini 4 Argon (High)"` (`gemini-4-argon`), `medium: "Gemini 3.8 Flash (Medium)"` (`gemini-3.8-flash`).
- **`gemini-cli`:** `large: "gemini-4-argon"`, `medium: "gemini-3.8-flash"`.

---

## 3. Adding or Updating an IAP Domain (e.g., `zup.com.br`)

Granting browser sign-in to an organization domain requires **4 coordinated layers** (granting IAP in the GCP Console alone is not enough):

| Step | Layer | Why It Is Required |
|---|---|---|
| **1** | **Google Workspace Primary/Verified Domain** | Every user's Google account email must belong to the Google Workspace domain (`hd` claim in the IAP JWT matches `zup.com.br`). |
| **2** | **GCP IAP IAM (`roles/iap.httpsResourceAccessor`)** | Must be bound on **all 3 Cloud Run services**: `scion-hub-proxy-acme`, `acme-observatory-web`, and `acme-observatory-api` for `domain:<domain>`. |
| **3** | **Scion Hub `authorized_domains` + `hub.db`** | Must list the domain in `/home/scion/.scion/settings.yaml` **and** in `hub_settings.access` (`origin='seeded'`) in `/home/scion/.scion/hub.db`, followed by `sudo systemctl restart scion-hub`. |
| **4** | **OAuth Brand Audience (`Internal` vs `External`)** | If the OAuth consent screen in `acme-airlines-factory` (`duboc.altostrat.com`) is set to **Internal**, Google OAuth rejects users from external Google Workspace orgs (`org_internal`) before IAP runs. Switch **APIs & Services → OAuth consent screen → User Type** to **External (In production)**; IAP IAM + Hub `authorized_domains` still block all public Gmail users. |

**Automated command:**
1. Add the domain to `WEB_DOMAINS` in [`infra/factory.env`](../../infra/factory.env):
   ```bash
   WEB_DOMAINS="google.com duboc.altostrat.com zup.com.br"
   ```
2. Run and verify:
   ```bash
   bash infra/50-web-access.sh && bash infra/verify.sh webaccess
   ```
