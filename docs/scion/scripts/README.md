# Scripts

| File | Purpose |
|---|---|
| [`bootstrap.sh`](bootstrap.sh) | Runs all factory steps in order, with a `verify.sh` check after each. Pauses at manual console steps. `--list`, `--from N`, `--only N`, `--force` |
| [`factory.env.example`](factory.env.example) | Template for `infra/factory.env`: every factory parameter (no secrets) |
| [`deploy-config.example.json`](deploy-config.example.json) | Template for `infra/deploy-config.json`, the answers for Scion's `deploy.sh` |

## The infra scripts (`infra/`)

All are idempotent, source `infra/lib.sh` (which loads `infra/factory.env`),
use the `acme-factory` gcloud configuration, and never prompt (except where a
secret is read).

| Script | Does | Check |
|---|---|---|
| `00-bootstrap-project.sh` | Project under `FOLDER_ID`, billing, APIs, `default` VPC | `project` |
| `10-deploy-hub.sh` | Clones Scion at `SCION_VERSION`, patches the zone, runs `deploy.sh --config` headless | `hub` |
| `20-model-access.sh` | `aiplatform.user` on the VM SA; builds `scion-claude` on the VM | `vertex`, `claude-image` |
| `25-agent-identity.sh` | Dedicated `acme-agent` SA (`assign` mode), removes `iam.serviceAccountAdmin` from VM SA, scopes deployer `run.developer`, enables `telemetry.cloud` | `identity`, `telemetry` |
| `30-hub-admin-access.sh enable\|disable\|status` | Temporary operator admin access (federation for gcloud ID tokens from `ORG_DOMAIN`) | — |
| `31-set-project-secret.sh <project-id> <KEY>` | Sets a hub project secret from **stdin** through the API | `hubproject` |
| `35-github-repo.sh` | Idempotent GitHub labels, pinned `🏭 Factory status` issue, `collaborators_only` interaction limit, and `main` branch protection | — |
| `40-build-team-templates.sh` | Builds `factory/templates/acme-*` from `factory/team/*` plus upstream `agent-team@AGENT_TEAM_SHA`; vendors impeccable. Non-lead roles get `max_duration`, `max_turns` and `max_model_calls` (3 × turns) | `cost` |
| `45-push-templates.sh [acme-<role> …]` | Pushes templates to the hub at global scope; opens and closes the admin window itself | `team` |
| `50-web-access.sh` | IAP `httpsResourceAccessor` for `WEB_DOMAINS`, plus the hub `domain_restricted` sign-in policy and `DEFAULT_USER_ROLE` (default `viewer`) | `webaccess` |
| `55-iap-oauth-client.sh [--reapply]` | Custom IAP OAuth client on all IAP services (prompts; Secret Manager) | `iap-oauth` |
| `60-factory.sh status\|tick-install\|pause\|resume` | Loop schedule (6h fallback) and the cost switch (never stops the VM) | `loop` |
| `70-budget-guard.sh` | Budget (`BUDGET_USD`), Pub/Sub, the agent-suspending guard on Cloud Run, its hub UAT | `budget` |
| `75-backups.sh` | Daily snapshot schedule (03:00 UTC, 7 days) on the hub VM boot disk | `backups` |
| `76-watchdog.sh` | Watchdog Cloud Run service + hourly (`0 * * * *`) Cloud Scheduler job + `04:00 UTC` reincarnation + PO email alert (`acme-factory-watchdog-po-alert`) | `watchdog` |
| `80-observatory-platform.sh` | Observatory platform (BigQuery, SAs, WIF, IAP, secrets, scheduler) | `observatory` |
| `90-irrops-demo-platform.sh` | Product demo service (IAP, minimal SA) — example of an app platform | `irrops-demo` |
| `95-agent-engine-platform.sh` | Vertex AI Agent Engine platform for app agents (runtime SA, bucket, deployer) | `agent-engine` |
| `90-teardown.sh [--delete-project]` | `deploy.sh --delete`, optionally deleting the project | — |
| `verify.sh <check>\|all\|e2e` | The factory test suite ([03-verification-and-eval.md](../03-verification-and-eval.md)) | — |
| `tests/test_verify.sh` | Unit tests for the `verify.sh` parsers (offline) | — |
| `budget-guard/` | Guard service source and tests (`uv run --with pytest pytest -q`) | — |
| `watchdog/` | Watchdog service source and tests (`uv run --with pytest pytest -q`) | — |

## Adapting to another product

`90-*` and `95-*` are product-specific examples (an IAP demo service, Agent
Engine). Keep them as patterns for your own app platform:
- a minimal runtime SA
- IAP-only access
- deployer rights scoped to one repo and one SA
- a platform contract doc for the agents
- a `verify.sh` check
