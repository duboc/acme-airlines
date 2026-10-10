# Addendum C: Observatory (factory cost and run dashboard)

- **Date:** 2026-10-09
- **Status:** Approved by the product owner (2026-10-09 23:22)
- **Extends:** addenda A and B. This is a second workstream of the same
  factory: same repo, same lead.

## Intent
A web dashboard where the product owner (and the IAP domains) can see what
the factory costs and what it is doing. The factory itself builds and deploys
it.

## Scope (all five panels)
| Panel | Source |
|---|---|
| Spend over time (VM, Cloud Run, Vertex AI; by day and service) | Cloud Billing standard export → BigQuery `billing_export` |
| Model usage (tokens/requests per model) | Cloud Monitoring `aiplatform.googleapis.com/publisher/online_serving/*` |
| Agent runs (phase per role, durations, LIMITS_EXCEEDED, lead ticks) | Hub API → collector → BigQuery `factory_ops.agent_snapshots` |
| GitHub flow (issues by status, PRs, merges/day, verdicts, CI pass rate) | GitHub API → collector → BigQuery `factory_ops.github_snapshots` |
| Budget (spend vs $1000, thresholds, guard actions) | Billing export spend + config (`BUDGET_USD`) + guard logs (Cloud Logging) |

## Architecture
- **`acme-observatory`:** Cloud Run service behind **IAP**, for
  `domain:google.com` and `domain:duboc.altostrat.com`. It reads BigQuery,
  Monitoring and Logging. Runtime SA: `acme-observatory`, read-only.
- **`acme-observatory-collector`:** Cloud Run **job** with Direct VPC egress to
  the hub. **Cloud Scheduler** triggers it every 5 minutes. Runtime SA:
  `acme-observatory-collector`.
  - writes `factory_ops` only
  - reads secrets `acme-observatory-hub-token` (hub UAT: `agent:list`,
    `agent:read`) and `acme-observatory-github-token` (read-only GitHub PAT)
- **CD without keys:** GitHub Actions on `push: main` (paths `observatory/**`).
  It authenticates through **Workload Identity Federation** (pool `github`,
  provider `github-actions`, restricted to repo `duboc/acme-airlines` on
  `refs/heads/main`). It acts as deployer SA `acme-observatory-deployer`,
  pushes images to Artifact Registry `observatory`, and runs
  `gcloud run deploy|jobs deploy --image`.
- **Placeholders:** the platform script deploys placeholder service and job
  revisions, so IAP, the scheduler and IAM exist before the first app deploy.
  Workflows only swap images.
- **LLM:** none needed. Mocked fixtures are used for tests and local dev
  (`OBSERVATORY_DATA=fake`).

## Split of work
- **Operator (me):** `infra/80-observatory-platform.sh` (idempotent),
  `docs/observatory/platform-contract.md`, and `infra/verify.sh observatory`.
- **Factory:** one brief issue, with `area:observatory`, `status:triage`,
  `priority:p1`. The lead runs it as a parallel workstream through an
  `acme-eng-manager` with a worker budget, within the cap of 4. The team
  builds the collector, API and UI (impeccable), tests and the deploy
  workflow under `observatory/` and `.github/workflows/observatory-*.yml`.
- **Product owner (manual):**
  1. Turn on Billing **standard usage cost export** to project
     `acme-airlines-factory`, dataset `billing_export`.
  2. Create a read-only fine-grained GitHub PAT (Issues, PRs, Actions,
     Contents, Metadata: read) and add it to Secret Manager via stdin.

## Tasks
1. `verify.sh observatory` (RED), then the platform script, then GREEN.
2. The platform contract doc, plus the GitHub Actions variables (non-secret)
   set with `gh variable set`.
3. File the brief issue, and notify the lead.
4. Docs: runbook §8, operations, and ADR 0014.
