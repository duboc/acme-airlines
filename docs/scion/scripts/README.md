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
| `30-hub-admin-access.sh enable\|disable\|status` | Temporary operator admin access (federation for gcloud ID tokens from `ORG_DOMAIN`) | — |
| `31-set-project-secret.sh <project-id> <KEY>` | Sets a hub project secret from **stdin** through the API | `hubproject` |
| `40-build-team-templates.sh` | Builds `factory/templates/acme-*` from `factory/team/*` plus upstream `agent-team@AGENT_TEAM_SHA`; vendors impeccable | `cost` |
| `50-web-access.sh` | IAP `httpsResourceAccessor` for `WEB_DOMAINS`, plus the hub `domain_restricted` sign-in policy | browser |
| `55-iap-oauth-client.sh [--reapply]` | Custom IAP OAuth client on all IAP services (prompts; Secret Manager) | `iap-oauth` |
| `60-factory.sh status\|tick-install\|pause\|resume` | Loop schedule and the cost switch (never stops the VM) | `loop` |
| `70-budget-guard.sh` | Budget (`BUDGET_USD`), Pub/Sub, the agent-suspending guard on Cloud Run, its hub UAT | `budget` |
| `80-observatory-platform.sh` | Observatory platform (BigQuery, SAs, WIF, IAP, secrets, scheduler) | `observatory` |
| `90-irrops-demo-platform.sh` | Product demo service (IAP, minimal SA) — example of an app platform | `irrops-demo` |
| `95-agent-engine-platform.sh` | Vertex AI Agent Engine platform for app agents (runtime SA, bucket, deployer) | `agent-engine` |
| `90-teardown.sh [--delete-project]` | `deploy.sh --delete`, optionally deleting the project | — |
| `verify.sh <check>\|all\|e2e` | The factory test suite ([06-observability.md](../06-observability.md)) | — |
| `tests/test_verify.sh` | Unit tests for the `verify.sh` parsers (offline) | — |
| `budget-guard/` | Guard service source and tests (`uv run --with pytest pytest -q`) | — |

## Adapting to another product

`90-*` and `95-*` are product-specific examples (an IAP demo service, Agent
Engine). Keep them as patterns for your own app platform:
- a minimal runtime SA
- IAP-only access
- deployer rights scoped to one repo and one SA
- a platform contract doc for the agents
- a `verify.sh` check
