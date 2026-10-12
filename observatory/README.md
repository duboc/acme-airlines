# Observatory

Factory cost and run dashboard (design: `docs/superpowers/specs/2026-10-09-addendum-c-observatory.md`,
platform: `docs/observatory/platform-contract.md`).

```
observatory/
  collector/   # Cloud Run job: snapshots hub agents + GitHub flow into BigQuery factory_ops
  dashboard/   # Cloud Run service: read-only FastAPI (API.md is the UI contract); React UI in web/ (PRODUCT.md, DESIGN.md)
  fixtures/    # deterministic JSON rows, the shared contract for tests and fake mode
```

## CI

`.github/workflows/observatory-ci.yml` runs on PRs and pushes to `main` that touch
`observatory/**` or `.github/workflows/observatory-*.yml`. The main `CI` workflow is separate.
It sets `OBSERVATORY_DATA=fake`, uses no secrets and no GCP auth, and needs only package installs.

| Job | Directory | Steps |
|-----|-----------|-------|
| `collector` | `observatory/collector` | `uv sync --frozen`, `ruff check`, `ruff format --check`, `pytest` |
| `dashboard-api` | `observatory/dashboard` | same as `collector` |
| `dashboard-web` | `observatory/dashboard/web` | `npm ci`, `npm run lint`, `npm test`, `npm run build` |
| `docker-build` | `collector`, `dashboard` | `docker build` (no push), then `deploy/smoke-image.sh`: runs the image non-root, read-only, unwritable `$HOME`, `OBSERVATORY_DATA=fake`, and fails if the image runs `uv` at runtime (#191) |

A job whose project (or Dockerfile) does not exist yet logs `SKIPPED: ...` and passes.
Once the project exists, every step is enforced.

## Dashboard API

`dashboard/API.md` is the contract (endpoints, JSON shapes, `state` values). Run it locally with
fixtures: `OBSERVATORY_DATA=fake OBSERVATORY_SCENARIO=ok|empty|lagging|unavailable uv run uvicorn
dashboard.app:create_app --factory` in `observatory/dashboard`. Tests and lint are the same as the
collector's (`uv sync --frozen`, `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`).
Extra fixtures for it: `billing.json`, `monitoring.json`, `guard_logs.json`, `merges_per_day.json`.
Live mode (`OBSERVATORY_DATA` unset, the production default) uses `dashboard/gcp.py`
(`GcpDataSource`, read-only ADC): BigQuery billing export + `factory_ops`, Cloud Monitoring Vertex
token/request counts by `model_user_id`, Cloud Logging budget-guard lines. Results are cached for 60s;
GCP errors become `state: unavailable`, a missing billing export becomes `empty`. `merges_per_day` is
the daily max of `merges_24h` from `github_snapshots`.
The dashboard image only contains `observatory/dashboard`; fake mode needs the fixtures dir (local/tests).

## Deploy (`.github/workflows/observatory-deploy.yml`)

Runs on push to `main` touching `observatory/**` or the workflow itself, and on `workflow_dispatch`.
Keyless: WIF auth (`vars.OBSERVATORY_WIF_PROVIDER`, `vars.OBSERVATORY_DEPLOYER_SA`), no secrets or keys.
WIF trusts only `refs/heads/main`, so PR branches cannot deploy (and the workflow cannot be tested from
one). One deploy at a time (`concurrency`, no cancel-in-progress). A merge:

1. builds and pushes `dashboard:<sha>` (context `observatory/dashboard`) and `collector:<sha>`
   (context `observatory/collector`) to `vars.OBSERVATORY_AR_REPO`;
2. `gcloud run deploy acme-observatory --image dashboard:<sha>` (dashboard first);
3. `observatory/deploy/deploy-collector.sh` deploys the collector job: it probes
   `gcloud secrets versions list` (the deployer usually lacks access, so denial is expected). With an
   enabled version, or when the probe is denied, it runs `gcloud run jobs deploy ... --image collector:<sha>
   --update-secrets=GITHUB_TOKEN=acme-observatory-github-token:latest`. If that fails with "secret was not
   found", or the probe shows no enabled version, it redeploys with `--remove-secrets=GITHUB_TOKEN` and emits a
   `::warning::` (the GitHub snapshot stays skipped until the PAT has a version); the fallback matches only the `Secret .*acme-observatory-github-token.* was not found` error. Any other failure fails
   the job. A leftover mapping to a versionless secret would make *every* job deploy fail, which is why the
   mapping is removed rather than left. `--set-secrets`/`--clear-secrets` are never used: they replace all
   mappings and would drop `SCION_HUB_TOKEN`;
4. `observatory/deploy/verify-deploy.sh` (runs even if a deploy step failed, and never masks the failure)
   asserts both images end in `:<sha>` and the collector job still maps `SCION_HUB_TOKEN`.

`bash observatory/deploy/test_deploy.sh` tests these paths with a fake `gcloud` (run by Observatory CI,
job `deploy-scripts`). The workflow itself only runs on `main` (WIF), so use `workflow_dispatch` to re-run it.

Only `--image` changes otherwise: service accounts, IAP, networking and env vars are never passed.

## Collector

`python -m collector` ensures the `factory_ops` tables exist, then writes one row set per run
(all rows share one `ts`). It exits non-zero only when a snapshot step really fails.
The hub (`collector/hub.py`) and GitHub (`collector/github.py`) steps are live.
If the hub step fails (unreachable, 401/5xx, missing env), it logs a clear error, writes nothing for `agent_snapshots`, the other steps still run, and the exit code is non-zero.

| Env | Meaning |
|---|---|
| `OBSERVATORY_DATA=fake` | In-memory sink, rows come from `fixtures/`. No network or GCP. Used by tests and CI. |
| `HUB_ENDPOINT`, `HUB_PROJECT`, `SCION_HUB_TOKEN` | Hub base URL, project (UUID or slug, see below), Bearer token (scopes `agent:list`/`agent:read`). Live mode only. |
| `GITHUB_TOKEN`, `GITHUB_REPO` | Read-only token and repo (default `duboc/acme-airlines`). No token: `WARNING ... GitHub snapshot skipped`, no row, exit 0. |
| `PROJECT_ID`, `OPS_DATASET` | BigQuery target (`acme-airlines-factory`, `factory_ops`) |

Tests and lint (from `observatory/collector`):

```
uv sync --frozen
OBSERVATORY_DATA=fake uv run pytest
uv run ruff check && uv run ruff format --check
docker build observatory/collector
```

## Collector health

The collector image runs `/app/.venv/bin/python -m collector` directly: `uv` needs a writable
`$HOME` cache, which the no-home job user lacks (#191). The dashboard header shows **Collector
healthy** or a red **Collector down** alert, from `GET /api/collector-health`: red when the newest
agent/GitHub snapshot is over 15 minutes old (or none exists), naming the last execution's status
(read from the job's Cloud Logging lines; needs only the log read access the dashboard already has).

## Schema contract (`collector/schema.py`)

Both tables are partitioned by `DATE(ts)` (DAY partitioning on `ts`). `ts` is the collector run time
(UTC, ISO-8601 in fixtures/JSON). Creation is `create_table(exists_ok=True)`: never altered or dropped.

**`agent_snapshots`**: one row per hub agent per run.
`ts` TIMESTAMP (req), `name` STRING (req), `template`, `role`, `tier`, `phase`, `activity`,
`container_status` STRING, `started_at` TIMESTAMP, `last_activity` TIMESTAMP, `harness`, `model`
STRING, `limits_exceeded` BOOL.

**`github_snapshots`**: one row per run.
- `ts` TIMESTAMP (req)
- `issues_by_status` REPEATED `{status STRING, count INT64}`: open issues per `status:*` label (prefix stripped)
- `issues_by_label` REPEATED `{label STRING, count INT64}`: open issues per other label
- `open_prs` REPEATED `{number INT64, title, author STRING, created_at TIMESTAMP, latest_verdict STRING (APPROVE | REQUEST CHANGES | null), ci_conclusion STRING (success | failure | pending | null)}`
- `merges_24h` INT64 (req): PRs merged in the last 24 hours
- `ci_conclusions` REPEATED `{conclusion STRING, count INT64}`: workflow runs in the last 24 hours

## Hub snapshot (`collector/hub.py`)

`GET {HUB_ENDPOINT}/api/v1/projects/{projectUUID}/agents` with `Authorization: Bearer $SCION_HUB_TOKEN`
returns `{"agents": [...], "totalCount": n}` (the same call `scion list` makes). Timeout 10s, 3 attempts
with backoff on 5xx/transport errors; 4xx fails immediately. The token is never logged.

The path needs the project **UUID** (the slug 404s there). `HUB_PROJECT` may be either: a UUID is used
directly (e.g. `61b6ae3d-9335-4360-a259-5b306dc229f6`); a slug (`duboc-acme-airlines`) is resolved once
per run via `GET /api/v1/projects?slug=<slug>`. **Not verified with an `agent:list`/`agent:read`-only
UAT**: with an agent-scoped token that list returned an empty result, so slug resolution is believed
NOT to work with those scopes. On failure the collector logs an error naming `HUB_PROJECT` and
exits non-zero; the fix is to set `HUB_PROJECT` to the project UUID.

| `agent_snapshots` column | Hub field |
|---|---|
| `name`, `template` | `name`, `template` |
| `role` | `template` without the `acme-` prefix (`acme-backend` → `backend`) |
| `tier` | label `acme.team/tier` (`standing`/`ephemeral`); missing → `standing` |
| `phase`, `activity` | `phase`, `activity` (absent for suspended agents → null) |
| `container_status` | `containerStatus` |
| `started_at` | `startedAt` |
| `last_activity` | `lastActivityEvent` (set on `acme-lead` too: the lead's loop activity) |
| `harness` | `harnessConfig` |
| `model` | `appliedConfig.model` (not reported by the hub today → null) |
| `limits_exceeded` | `activity == "limits_exceeded"` (the LIMITS_EXCEEDED state) |

`fixtures/hub_agents.json` is a sanitised real response (ids, owners, tasks and broker details
removed; `acme-reviewer` edited to the `limits_exceeded` state as a synthetic case). In fake mode the
collector maps it with the same code. `fixtures/agent_snapshots.json` is unchanged (dashboard fixture).

## GitHub snapshot (`collector/github.py`)

One `github_snapshots` row per run, from read-only REST calls (paginated, 100 per page, at most 5 pages):
open issues (`/issues`, PRs excluded; `status:*` labels → `issues_by_status`, every other label →
`issues_by_label`), open PRs (`/pulls`), closed PRs sorted by update (merged in the last 24h →
`merges_24h`), `/actions/runs` created in the last 24h (`success`, `failure`, everything else
`other` → `ci_conclusions`), and per open PR its reviews and issue comments.
`latest_verdict`: the most recent review/comment whose first line is `VERDICT: APPROVE` or
`VERDICT: REQUEST CHANGES` (latest wins). PR `ci_conclusion` from runs on the head SHA: any failure →
`failure`, any unfinished → `pending`, all success → `success`, no runs → null.
A missing token or any GitHub error (401/403/429/5xx/network) logs a `GitHub snapshot skipped`
warning and writes no row; the hub snapshot is unaffected and the exit code stays 0. The token is
never logged. Fake mode serves the sanitised recordings in `fixtures/github/` through the same code.
Schema unchanged.

## Fixtures

`fixtures/agent_snapshots.json` and `fixtures/github_snapshots.json` are JSON arrays of rows in the
shape above. A test validates every row with `schema.validate_row`; the dashboard's fake mode reads
the same files, so change the schema and fixtures together.

## Sinks

`Sink.write(table, rows)`: `BigQuerySink` (streaming insert, raises on row errors) and `FakeSink`
(in-memory dict, selected by `OBSERVATORY_DATA=fake`).

## Dashboard UI

`dashboard/web/` is React + Vite + TS. `cd observatory/dashboard/web && npm ci && npm run dev` proxies `/api`
to `localhost:8080` (run the API with `OBSERVATORY_DATA=fake`). Set `VITE_DATA_MODE=fake` at build/dev time to show the
"Fake data" header badge (the API contract carries no mode field). The Dockerfile builds `web/dist` in a node stage;
the image sets `DASHBOARD_STATIC=/app/web/dist` and FastAPI serves it at `/`. Panel bodies plug into `Panel` as render children (#119/#120).

Panels: Spend (stacked SVG bars + daily table), Model usage (24h/7d), Budget (meter with 50/90/100% markers, textual threshold, guard actions). Billing `empty` shows "Billing export not enabled yet".

Panels (cont.): Agents (grouped by role, lead staleness >30 min, LIMITS_EXCEEDED text flag) and GitHub (CI pass rate, issues by status, open PRs with latest verdict, 14-day merges + table). Empty GitHub shows "GitHub token isn't configured yet".
