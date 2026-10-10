# Observatory: platform contract

This is everything the Observatory workstream can rely on. The operator
provisioned it with `infra/80-observatory-platform.sh`; design in
[addendum C](../superpowers/specs/2026-10-09-addendum-c-observatory.md).

> [!IMPORTANT]
> **Agents never change IAM, WIF, IAP, secrets or the scheduler.** If you need
> a platform change, file an issue with label `area:observatory` +
> `status:blocked` and say "needs operator".

## Code layout (owned by the team)

```
observatory/
  collector/        # Cloud Run JOB: snapshot hub agents and GitHub flow into BigQuery
  dashboard/        # Cloud Run SERVICE: API and UI (designed with the impeccable skill)
  fixtures/         # deterministic fake data for tests and local dev
.github/workflows/
  observatory-ci.yml       # PRs touching observatory/**: lint and tests (OBSERVATORY_DATA=fake)
  observatory-deploy.yml   # push to main touching observatory/**: build, push and deploy (WIF, no keys)
```

## Runtime resources (already exist)

| Resource | Name | Notes |
|---|---|---|
| Project / region | `acme-airlines-factory` / `us-central1` | |
| Dashboard service | Cloud Run `acme-observatory` | **Behind IAP** (domains `google.com`, `duboc.altostrat.com`). It runs a placeholder until the first deploy. Keep `--no-allow-unauthenticated`; never disable IAP. |
| Collector job | Cloud Run job `acme-observatory-collector` | Direct VPC egress to the hub. **Cloud Scheduler** `acme-observatory-collect` runs it every 5 minutes. |
| Dashboard SA | `acme-observatory@acme-airlines-factory.iam.gserviceaccount.com` | Read-only. BigQuery `jobUser`, READER on `billing_export` and `factory_ops`, `monitoring.viewer`, `logging.viewer`, `compute.viewer` on the hub VM. |
| Collector SA | `acme-observatory-collector@…` | BigQuery `jobUser`, WRITER on `factory_ops`, accessor on the two secrets below. |
| Image repo | `us-central1-docker.pkg.dev/acme-airlines-factory/observatory` | Images `dashboard` and `collector`. |

## Data

| Dataset | Producer | Content |
|---|---|---|
| `billing_export` (US) | Cloud Billing **standard usage cost export** | Table `gcp_billing_export_v1_<BILLING_ACCOUNT_ID with _>`. **Data lags a few hours to a day.** The table appears only after the product owner turns on the export. Handle "table not found" gracefully. |
| `factory_ops` (US) | the collector (you create the tables) | Suggested: `agent_snapshots` (ts, name, template, role, tier, phase, activity, container_status, started_at, last_activity, harness, model) and `github_snapshots` (ts, issues by status/label, open PRs, merges in the last 24h, latest verdict per PR, CI conclusion counts). Partition by `DATE(ts)`. |
| Cloud Monitoring | Vertex AI | `aiplatform.googleapis.com/publisher/online_serving/token_count`, `…/model_invocation_count` (labels include the model). Query with the Monitoring API using the dashboard SA. |
| Cloud Logging | budget guard | `resource.labels.service_name="acme-budget-guard"`, `textPayload:"cost="`. Each line reads `cost=X budget=Y -> no action|agents suspended`. |

## Environment variables (set on the placeholders; keep them when deploying)

| Var | Where | Value |
|---|---|---|
| `PROJECT_ID` | both | `acme-airlines-factory` |
| `BUDGET_USD` | dashboard | `1000` |
| `BILLING_DATASET` / `OPS_DATASET` | dashboard / both | `billing_export` / `factory_ops` |
| `HUB_ENDPOINT` | collector | `http://<hub VM internal IP>:8080`, reachable only from the VPC |
| `HUB_PROJECT` | collector | `61b6ae3d-9335-4360-a259-5b306dc229f6`, the hub project **UUID**. The hub REST API `GET /api/v1/projects/<id>/agents` rejects the slug `duboc-acme-airlines` (404) and the agent-scoped UAT cannot resolve slugs. Set by `infra/80` and re-applied on every deploy with `--update-env-vars` (never `--set-env-vars`); `verify-deploy.sh` asserts it. |
| `GITHUB_REPO` | collector | `duboc/acme-airlines` |
| `SCION_HUB_TOKEN` | collector (secret) | `acme-observatory-hub-token`. A hub UAT with **`agent:list`, `agent:read`**, project-scoped, 90 days. Use it as `Authorization: Bearer`, or with the scion CLI (`SCION_HUB_ENDPOINT` + `SCION_HUB_TOKEN`). |
| `GITHUB_TOKEN` | collector (secret, **add to the job on first deploy**: `--update-secrets=GITHUB_TOKEN=acme-observatory-github-token:latest`; not `--set-secrets`/`--clear-secrets`, which replace all of the job's secret mappings and would drop `SCION_HUB_TOKEN`). While the secret has no version, the deploy removes the mapping with `--remove-secrets=GITHUB_TOKEN`: a mapping to a versionless secret makes every later job deploy fail with "secret ... was not found" | A read-only GitHub PAT, provided by the product owner. Until it exists, skip the GitHub snapshot and log a warning. |
| `OBSERVATORY_DATA` | tests / local | `fake` makes everything read `observatory/fixtures/`. **CI must use `fake`.** |

## Deploy (GitHub Actions, keyless)

These repository **variables** already exist (not secrets):
`OBSERVATORY_WIF_PROVIDER`, `OBSERVATORY_DEPLOYER_SA`, `OBSERVATORY_PROJECT`,
`OBSERVATORY_REGION`, `OBSERVATORY_AR_REPO`.

```yaml
permissions: { contents: read, id-token: write }
steps:
  - uses: actions/checkout@v4
  - uses: google-github-actions/auth@v2
    with:
      workload_identity_provider: ${{ vars.OBSERVATORY_WIF_PROVIDER }}
      service_account: ${{ vars.OBSERVATORY_DEPLOYER_SA }}
  - uses: google-github-actions/setup-gcloud@v2
  - run: gcloud auth configure-docker us-central1-docker.pkg.dev --quiet
  - run: |
      IMG=${{ vars.OBSERVATORY_AR_REPO }}/dashboard:${{ github.sha }}
      docker build -t "$IMG" observatory/dashboard && docker push "$IMG"
      gcloud run deploy acme-observatory --image "$IMG" --region ${{ vars.OBSERVATORY_REGION }} --project ${{ vars.OBSERVATORY_PROJECT }}
  # collector: same pattern, with `gcloud run jobs deploy acme-observatory-collector --image …`
```

- **WIF scope:** it trusts only `duboc/acme-airlines` on `refs/heads/main`.
  Deploys from PR branches fail by design.
- **Deployer permissions:** `run.developer`, `artifactregistry.writer` on
  `observatory`, and `iam.serviceAccountUser` on the two runtime SAs. Nothing
  else.
- **Don't re-specify** `--service-account`, `--iap`, VPC flags or secrets on
  `gcloud run deploy`. The existing settings are kept when you only change
  `--image`. The allowed exceptions are the collector's `GITHUB_TOKEN` secret
  (`--update-secrets`) and its `HUB_PROJECT` env var (`--update-env-vars`).
