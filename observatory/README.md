# Observatory

Factory cost and run dashboard (design: `docs/superpowers/specs/2026-10-09-addendum-c-observatory.md`,
platform: `docs/observatory/platform-contract.md`).

```
observatory/
  collector/   # Cloud Run job: snapshots hub agents + GitHub flow into BigQuery factory_ops
  dashboard/   # Cloud Run service: read-only FastAPI (API.md is the UI contract); UI in web/ later
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
| `docker-build` | `collector`, `dashboard` | `docker build` (no push) |

A job whose project (or Dockerfile) does not exist yet logs `SKIPPED: ...` and passes.
Once the project exists, every step is enforced.

## Dashboard API

`dashboard/API.md` is the contract (endpoints, JSON shapes, `state` values). Run it locally with
fixtures: `OBSERVATORY_DATA=fake OBSERVATORY_SCENARIO=ok|empty|lagging|unavailable uv run uvicorn
dashboard.app:create_app --factory` in `observatory/dashboard`. Tests and lint are the same as the
collector's (`uv sync --frozen`, `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`).
Extra fixtures for it: `billing.json`, `monitoring.json`, `guard_logs.json`, `merges_per_day.json`.
The dashboard image only contains `observatory/dashboard`; fake mode needs the fixtures dir (local/tests).

## Collector

`python -m collector` ensures the `factory_ops` tables exist, then writes one row set per run
(all rows share one `ts`). It exits non-zero only when a snapshot step really fails.
Live hub / GitHub sources arrive in later slices; until then live mode logs a warning and skips them.

| Env | Meaning |
|---|---|
| `OBSERVATORY_DATA=fake` | In-memory sink, rows come from `fixtures/`. No network or GCP. Used by tests and CI. |
| `PROJECT_ID`, `OPS_DATASET` | BigQuery target (`acme-airlines-factory`, `factory_ops`) |

Tests and lint (from `observatory/collector`):

```
uv sync --frozen
OBSERVATORY_DATA=fake uv run pytest
uv run ruff check && uv run ruff format --check
docker build observatory/collector
```

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

## Fixtures

`fixtures/agent_snapshots.json` and `fixtures/github_snapshots.json` are JSON arrays of rows in the
shape above. A test validates every row with `schema.validate_row`; the dashboard's fake mode reads
the same files, so change the schema and fixtures together.

## Sinks

`Sink.write(table, rows)`: `BigQuerySink` (streaming insert, raises on row errors) and `FakeSink`
(in-memory dict, selected by `OBSERVATORY_DATA=fake`).
