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
The hub step (`collector/hub.py`) is live; the GitHub source arrives in a later slice and live mode logs a warning and skips it until then.
If the hub step fails (unreachable, 401/5xx, missing env), it logs a clear error, writes nothing for `agent_snapshots`, the other steps still run, and the exit code is non-zero.

| Env | Meaning |
|---|---|
| `OBSERVATORY_DATA=fake` | In-memory sink, rows come from `fixtures/`. No network or GCP. Used by tests and CI. |
| `HUB_ENDPOINT`, `HUB_PROJECT`, `SCION_HUB_TOKEN` | Hub base URL, project (UUID or slug, see below), Bearer token (scopes `agent:list`/`agent:read`). Live mode only. |
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

## Fixtures

`fixtures/agent_snapshots.json` and `fixtures/github_snapshots.json` are JSON arrays of rows in the
shape above. A test validates every row with `schema.validate_row`; the dashboard's fake mode reads
the same files, so change the schema and fixtures together.

## Sinks

`Sink.write(table, rows)`: `BigQuerySink` (streaming insert, raises on row errors) and `FakeSink`
(in-memory dict, selected by `OBSERVATORY_DATA=fake`).
