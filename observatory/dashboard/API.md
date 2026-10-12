# Dashboard API contract

Read-only JSON API served by `acme-observatory` (FastAPI). This file is the contract the UI codes
against. Models are in `src/dashboard/models.py`. Only `GET` exists; any other method returns `405`.
Invalid query values return `422`. All timestamps are UTC ISO-8601 with `Z` (`2026-10-10T12:00:00Z`);
`date` fields are `YYYY-MM-DD`. Money is USD floats.

## Envelope (every `/api/*` response)

| Field | Type | Meaning |
|---|---|---|
| `state` | `"ok" \| "empty" \| "lagging" \| "unavailable"` | see below |
| `as_of` | string \| null | time of the newest data shown; null when there is none |
| `message` | string \| null | human text; set when `state` is not `ok` |

- `ok`: data is present and fresh.
- `empty`: the source is readable but has no rows (e.g. the collector has not run). Payload lists are empty.
- `lagging`: data is present but stale (the newest billing day is over 2 days old; collector snapshots older than 15 min). Render the data with a warning showing `message`.
- `unavailable`: the source cannot be read (e.g. billing export not enabled). HTTP status is still `200`. Payload is empty or zero.

Payload fields below are always present (empty list / zero / null when there is no data).

## `GET /healthz`
`{"status": "ok"}`

## `GET /api/collector-health`
```json
{"state":"lagging","as_of":"2026-10-10T11:00:00Z","message":"The collector has not written a snapshot for over 15 minutes. The last execution failed.",
 "healthy":false,"snapshot_age_seconds":3630,
 "last_execution":{"ts":"2026-10-10T12:00:00Z","status":"failed","detail":"Container called exit(2)."}}
```
`healthy` is false (render red) when the newest agent/GitHub snapshot is over 15 min old: `state`
is `lagging`; `empty` when none exists ("never written"); `unavailable` when it cannot be read.
`last_execution.status` is `succeeded | failed` (null when no job logs are found).

## `GET /api/spend?days=30` (1..365)
```json
{"state":"ok","as_of":"2026-10-09T00:00:00Z","message":null,"days":30,"currency":"USD",
 "series":[{"date":"2026-10-09","vm":14.4,"cloud_run":1.7,"vertex_ai":47.0,"other":0.47,"total":63.57}],
 "totals":{"date":"total","vm":0,"cloud_run":0,"vertex_ai":0,"other":0,"total":0}}
```
`series` is ascending by date; `vm` = Compute Engine, `other` = every other service. `as_of` is the newest usage day.

## `GET /api/models?hours=24` (1..720)
```json
{"state":"ok","as_of":"2026-10-10T12:00:30Z","message":null,"hours":24,
 "models":[{"model":"sonnet","label":"Sonnet","tokens":9800000,"requests":1450}]}
```
`model` is `opus | sonnet | haiku | gemini_flash | other`; sorted by `tokens` descending.

## `GET /api/agents`
```json
{"state":"ok","as_of":"2026-10-10T12:00:00Z","message":null,
 "lead_last_activity":"2026-10-10T11:58:00Z","limits_exceeded_count":1,
 "agents":[{"name":"acme-backend","template":"acme-backend","role":"backend","tier":"sonnet",
   "phase":"running","activity":"executing","container_status":"running",
   "started_at":"2026-10-10T10:30:00Z","last_activity":"2026-10-10T11:59:30Z",
   "harness":"claude","model":"claude-sonnet-5-5","limits_exceeded":false,"running_seconds":5400}]}
```
Latest snapshot per agent, sorted by `name`. `running_seconds` is `as_of - started_at` while
`phase == "running"`, else `last_activity - started_at`; null if unknown. `lead_last_activity` is that of the agent with `role == "lead"`.

## `GET /api/github`
```json
{"state":"ok","as_of":"2026-10-10T12:00:00Z","message":null,
 "snapshot":{"ts":"2026-10-10T12:00:00Z",
   "issues_by_status":[{"key":"ready","count":3}],"issues_by_label":[{"key":"role:backend","count":4}],
   "open_prs":[{"number":114,"title":"...","author":"...","created_at":"2026-10-10T11:00:00Z",
     "latest_verdict":"APPROVE","ci_conclusion":"success"}],
   "merges_24h":6,"ci_conclusions":[{"key":"success","count":14}],"ci_pass_rate":0.875},
 "merges_per_day":[{"date":"2026-10-04","count":3}]}
```
`snapshot` is null unless data exists. `latest_verdict`: `APPROVE | REQUEST CHANGES | null`;
`ci_conclusion`: `success | failure | pending | null`. `ci_pass_rate` is 0..1 or null. `merges_per_day`
is ascending, up to 14 days.

## `GET /api/budget`
```json
{"state":"ok","as_of":"2026-10-09T00:00:00Z","message":null,"budget_usd":1000.0,
 "mtd_usd":528.98,"percent":52.9,
 "markers":[{"percent":50,"usd":500.0,"reached":true},{"percent":90,"usd":900.0,"reached":false},
            {"percent":100,"usd":1000.0,"reached":false}],
 "guard_actions":[{"ts":"2026-10-10T11:00:09Z","cost_usd":480.0,"budget_usd":1000.0,"action":"no action"}]}
```
`mtd_usd` is billing spend since the 1st (UTC). `guard_actions`: up to 20 most recent budget-guard
log lines, newest first; `action` is `no action | agents suspended`. `budget_usd` comes from `BUDGET_USD`.

## `GET /api/agent-engine?hours=1` (1..168)
```json
{"state":"ok","as_of":"2026-10-10T12:00:30Z","message":null,"hours":1,
 "engines":[{"name":"acme-customer-agent","engine_id":"1111111111","requests":200,"errors":10,
   "error_rate":0.05,"latency_p50_ms":850.0,"latency_p95_ms":2400.0,"trace_access":"granted",
   "traces":[{"trace_id":"4bf9...","name":"invoke_agent","start_time":"2026-10-10T11:58:00Z",
     "duration_ms":1830.5,"status":"ok",
     "console_url":"https://console.cloud.google.com/traces/list?project=acme-airlines-factory&tid=4bf9..."}]}]}
```
- **Discovery:** from config, not the `aiplatform` list API (the dashboard SA has no `aiplatform.viewer`).
  Set `AGENT_ENGINES="acme-customer-agent=<reasoning_engine_id>,acme-sales-agent=<id>"` on the service.
  Entries with an empty or invalid name (`[a-zA-Z0-9_-]+`) or id (digits only) are skipped.
  No config means `state: "empty"` and `engines: []`.
- **Monitoring** (resource `aiplatform.googleapis.com/ReasoningEngine`, filtered by
  `resource.labels.reasoning_engine_id`): `aiplatform.googleapis.com/reasoning_engine/request_count`
  (label `response_code`; `errors` = non-2xx, `error_rate = errors / requests`, null with no requests) and
  `.../reasoning_engine/request_latencies` (ms; `ALIGN_PERCENTILE_50/95` over the window). If several series
  match, the largest percentile is shown. Confirm the metric types against the live project on first deploy.
- **Traces** (Cloud Trace v1, root spans, window = `hours` capped at 24h, newest first, up to 10): filter `+service.name:"<display name>"`.
  Only trace id, root span name, start, duration and status (`error` if the span has an `error` label or `/http/status_code` >= 400) are
  returned; no span attribute values.
- **Degradation:** if Trace returns 403 (the SA lacks `cloudtrace.user`, issue #158) the engine has
  `"traces": null, "trace_access": "not_granted"`; metrics still return.

## Fake mode and scenarios

`OBSERVATORY_DATA=fake` reads `observatory/fixtures/` (override with `OBSERVATORY_FIXTURES`), with a
fixed clock of `2026-10-10T12:00:30Z`. `OBSERVATORY_SCENARIO` selects the state the UI sees:
`ok` (default), `empty` (no rows), `lagging` (same data, clock +3 days), `unavailable` (reads fail).

```
cd observatory/dashboard
OBSERVATORY_DATA=fake OBSERVATORY_SCENARIO=lagging uv run uvicorn dashboard.app:create_app --factory
```

The built UI (`web/dist`, or `DASHBOARD_STATIC`) is served at `/` when present.
