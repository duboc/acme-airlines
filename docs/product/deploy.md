# Deployment: local containers and Cloud Run (documented, not deployed)

Status: the **local containers are the verified deliverable**. Cloud Run deployment is **documented
only** in the MVP (ADR 0100 Decision 1): nothing here was deployed and no deploy identity exists.

## Local: docker compose

```bash
export ACME_SIM_TOKENS="customer=$(openssl rand -hex 24),sales=$(openssl rand -hex 24),test-admin=$(openssl rand -hex 24)"
export ACME_SALES_TOKEN=$(echo "$ACME_SIM_TOKENS" | sed -n 's/.*sales=\([^,]*\).*/\1/p')
docker compose -f app/compose.yaml up --build     # then open http://localhost:8080
```

| Service | Image / command | Network | Notes |
|---|---|---|---|
| `frontend` | `app/frontend/Dockerfile` (nginx-unprivileged, uid 101) | web-net, published on `127.0.0.1:8080` | Proxies `/api/*` to the runtime; `/api/a2a` and `/.well-known` are blocked (404). |
| `customer-runtime` | `app/backend/Dockerfile`, `acme-customer` | web-net, a2a-net | Runtime API, Customer agent, A2A skills, in-process simulators. Not published. |
| `sales-agent` | same image, `acme-sales` | sim-net, a2a-net | Holds **only** the `sales` token; `acme-sim` returns 403 for every server except `inventory`. No route to the browser network. |
| `acme-sim` | same image, `acme-sim` | sim-net only | Per-caller tokens (`ACME_SIM_TOKENS`). Not published. |

All containers are non-root (uid 10001, or 101 for nginx), run read-only with `cap_drop: ALL` and
`no-new-privileges`. Images contain no secrets and no `.env` files (`.dockerignore` excludes them);
tokens only come from the environment at run time (`ACME_SIM_TOKENS` and `ACME_SALES_TOKEN` are secrets, set at deploy). Both `acme-customer` (in local mode) and `acme-sales` fail closed and refuse to start without `ACME_SALES_TOKEN`, and `SalesRouteGuard` rejects any Sales-credentialed request outside `POST /a2a` with `403` and an audit event (#230). `sim-net` and `a2a-net` are `internal` networks. `customer-runtime` has a fixed address on each (`web-net` `10.89.1.2`, `a2a-net` `10.89.2.2`) and each listener binds one address only: the passenger API (`ACME_CUSTOMER_HOST=10.89.1.2`, `ACME_CUSTOMER_PORT=8800`) on `web-net`, and the A2A-only listener (`ACME_A2A_HOST=10.89.2.2`, `ACME_A2A_PORT=8802`) on `a2a-net`, so `sales-agent` (`a2a-net`, `sim-net`) cannot connect to 8800 at all (#265). In local mode, `ACME_CUSTOMER_LOOP` (default `1`, `0` disables) runs the 1 s background `CustomerLoop` (OCC poll, scheduled proactive WhatsApps, no-offer closure), keeping `GET /journeys/{id}` and `GET /whatsapp/{id}/messages` strictly read-only (#228). On Cloud Run, serve 8802 as a separate service from the same image (or keep it behind IAM).

Demo limitation: the Customer runtime keeps its own in-process simulators (the runtime reads and
writes that world directly), so the standalone `acme-sim` serves the Sales agent's inventory view
from the same seed but a separate state; an accepted upgrade is not reflected in Sales' copy.

`LLM_MODE` defaults to `fake` (offline, $0). The live demo only:

```bash
LLM_MODE=vertex docker compose -f app/compose.yaml up --build
```

Vertex uses ADC only (no API keys, ever), project `acme-airlines-factory`, **`ACME_GENAI_LOCATION=global`**
(regional locations make the service refuse to start). Each Vertex call is bounded by `LLM_TIMEOUT_SECONDS` (default `8.0`s, #231): a timed-out compose call falls back to the deterministic template, and a timed-out passenger-reply parse fails closed to `Other` (no acknowledgement, no offer, escalated to a human). Provide ADC by mounting your credentials
read-only into `customer-runtime` and setting `GOOGLE_APPLICATION_CREDENTIALS` in a local override file,
never in this repo.

## Hosted demo (IAP)

The operator creates the IAP-protected `acme-irrops-demo` Cloud Run platform with
`infra/90-irrops-demo-platform.sh` (see the [Scion & GCP setup guide](../scion/01-scion-and-gcp-setup.md)).

## Single-container demo image (`app/demo/Dockerfile`)

One image for the hosted demo: the built frontend, the runtime API, `acme-sim` and the Sales agent.
`acme-demo` starts the three Python services as loopback-only child processes and serves a front
proxy on `$PORT` (default 8080). `/api/a2a`, `/api/.well-known` and `/mcp/*` return 404 (also when
case-changed or percent-encoded). Per-caller sim tokens are generated at startup, passed to each
child through its own environment only, and never logged. Non-root (uid 10001), `LLM_MODE=fake`.

```bash
docker build -f app/demo/Dockerfile -t acme-demo app && docker run --rm -p 127.0.0.1:8080:8080 acme-demo
```

`.github/workflows/irrops-deploy.yml` builds it on every push to main touching `app/**`, pushes it to
`IRROPS_AR_REPO` and swaps only the image on Cloud Run service `IRROPS_DEMO_SERVICE` (keyless WIF,
`OBSERVATORY_*` variables), then checks the deployed image SHA. If the service does not exist yet it
warns and succeeds. The `containers` CI job builds the image and smokes it (cancellation -> RESOLVED,
`/api/a2a` -> 404).

### Engine mode (`ENGINE_MODE`)

`ENGINE_MODE=local` (default) runs the agents in the container. `ENGINE_MODE=agent-engine` serves
the same API from the deployed Agent Engines: the supervisor starts only the API child (no local
sim, Sales or agents), and the API calls the Customer engine (`AGENT_ENGINE_CUSTOMER`) and then
the Sales engine (`AGENT_ENGINE_SALES`) with ADC. Both must be full resource names
(`projects/<p>/locations/<l>/reasoningEngines/<id>`), written by the engine deploy workflow (#163);
without them (or with a name outside `GOOGLE_CLOUD_PROJECT` or its number and
`AGENT_ENGINE_LOCATION`, default `us-central1`) the app still starts and listens (`/api/healthz` reports `engine: agent-engine`). Engines are resolved lazily on the first engine call; a bad name or missing ADC is a clear `503` then, retried on the next request (#199). CI smoke-tests this mode with dummy names and no credentials. The full UI works in this
mode: scenarios, journeys, the WhatsApp phone panel, reset and trigger all go to the Customer engine. Within an engine container (`max_instances=1`), worker processes share state via `ACME_STATE_DB=/tmp/acme-state/state.db` (#200), and `EngineBackend` sends `{preset, step, tape}` on every scenario call so an unseeded or restarted container deterministically rebuilds state by offline replay and replays recorded LLM answers from the `RecordingLlm`/`TapeLlm` replay tape ([ADR 0103](adr-0103-stateless-engine-scenarios.md), #266/#279/#280).

`irrops-deploy.yml` sets `ENGINE_MODE` after swapping the image, using
`app/demo/select-engine-mode.sh <HOSTED_ENGINE_MODE> <service env names>`. `HOSTED_ENGINE_MODE` is
committed in the workflow as **`auto`**: `agent-engine` only if the service already has both
`AGENT_ENGINE_*` variables, otherwise `local`, so a missing or broken engine deploy never breaks the
hosted demo. Set it to `local` to force the in-container agents. Tested in
`tests/api/test_engine_mode.py`.

## Cloud Run (target, not deployed)

One Cloud Run service per container, one service account per service, **no unauthenticated access**:

```bash
PROJECT=acme-airlines-factory; REGION=<a Cloud Run region>   # the Vertex location stays `global`
for SVC in customer-runtime sales-agent acme-sim frontend; do
  gcloud iam service-accounts create "$SVC" --project "$PROJECT"
done
gcloud run deploy acme-sim --image <registry>/acme-backend --command acme-sim --no-allow-unauthenticated \
  --service-account acme-sim@$PROJECT.iam.gserviceaccount.com --set-env-vars ACME_SIM_HOST=0.0.0.0 \
  --set-secrets ACME_SIM_TOKENS=acme-sim-tokens:latest
gcloud run deploy customer-runtime --image <registry>/acme-backend --command acme-customer --no-allow-unauthenticated \
  --service-account customer-runtime@$PROJECT.iam.gserviceaccount.com \
  --set-env-vars ACME_CUSTOMER_HOST=0.0.0.0,LLM_MODE=vertex,GOOGLE_CLOUD_PROJECT=$PROJECT,ACME_GENAI_LOCATION=global \
  --set-secrets ACME_SALES_TOKEN=acme-sales-token:latest   # required: the runtime refuses to start without it (#230)
gcloud run deploy sales-agent --image <registry>/acme-backend --command acme-sales --no-allow-unauthenticated \
  --service-account sales-agent@$PROJECT.iam.gserviceaccount.com \
  --set-env-vars ACME_SALES_HOST=0.0.0.0,ACME_SIM_URL=<acme-sim url>,ACME_CUSTOMER_URL=<customer-runtime url> \
  --set-secrets ACME_SALES_TOKEN=acme-sales-token:latest
```

Env: `ACME_GENAI_LOCATION=global` always; **never** set `GOOGLE_API_KEY` or `GEMINI_API_KEY`
(the app refuses to start); model access is through the service account (ADC), granted
`roles/aiplatform.user` on the `customer-runtime` service account only.

### Service-to-service IAM (required)

`roles/run.invoker` is granted per service, to specific service accounts only:

```bash
# Only the sales-agent identity (and the frontend) may invoke the Customer runtime.
gcloud run services add-iam-policy-binding customer-runtime --member serviceAccount:sales-agent@$PROJECT.iam.gserviceaccount.com --role roles/run.invoker
gcloud run services add-iam-policy-binding customer-runtime --member serviceAccount:frontend@$PROJECT.iam.gserviceaccount.com --role roles/run.invoker
# The sales-agent may reach acme-sim (the app token then limits it to `inventory`); the customer too.
gcloud run services add-iam-policy-binding acme-sim --member serviceAccount:sales-agent@$PROJECT.iam.gserviceaccount.com --role roles/run.invoker
gcloud run services add-iam-policy-binding acme-sim --member serviceAccount:customer-runtime@$PROJECT.iam.gserviceaccount.com --role roles/run.invoker
```

`acme-sim` and `customer-runtime` are never `allUsers`/`allAuthenticatedUsers`. This is what backs the
offer's `origin="sales"` claim with identity (the claim itself is only a payload field).

Known gaps to close before a real deployment (follow-ups, not done here):
1. Clients must send the Cloud Run ID token in **`X-Serverless-Authorization`** (the `Authorization` header
   carries the sim bearer token); the HTTP connectors do not do this yet (#100).
2. IAM is per service, not per path: the frontend's invoker role would also let it call `/a2a`. The nginx
   config blocks it, but an app-level check of the caller identity on `/a2a` (the `sales-agent` service
   account only) is still needed (#101).
3. The runtime's in-process simulators must be replaced by `acme-sim` calls for a split deployment (#102).

## Agent Engine

The IRROPS agents can also run on Vertex AI Agent Engine, deployed by `.github/workflows/agent-engine-deploy.yml` on push to `main`. See [Agent Engine apps: Deploy](engines.md#deploy) and [ADR 0103](adr-0103-stateless-engine-scenarios.md).

## Configuration reference

Every `ACME_*` and `LLM_*` environment variable read in `app/backend/src` (enforced by `app/backend/tests/unit/test_config_reference.py`):

| Name | Purpose | Default | Required? | Fail-closed behaviour | Where read |
|---|---|---|---|---|---|
| `ACME_A2A_HOST` | Bind address for the Customer runtime's A2A-only listener (`10.89.2.2` on `a2a-net` in Compose) | `ACME_CUSTOMER_HOST` (`127.0.0.1`) | No | Binds only the configured interface so Sales on `a2a-net` cannot reach port `8800` on `web-net` (#265) | `acme/customer/main.py` |
| `ACME_A2A_PORT` | Port for the Customer runtime's A2A-only listener (`/a2a` and agent card only) | `8802` | No | Non-integer raises `ValueError` at startup; serves no passenger routes (#230) | `acme/customer/main.py`, `acme/demo/supervisor.py` |
| `ACME_CUSTOMER_ENGINE` | Full Vertex AI Reasoning Engine resource name (`projects/<p>/locations/<l>/reasoningEngines/<id>`) for the Customer engine called by Sales | `""` | Yes when `ENGINE_MODE=agent-engine` | Raises `ValueError` (captured as `startup_error` on the Sales engine) if missing or not a valid `reasoningEngines` resource name | `acme/sales/client.py`, `acme/engines/sales.py`, `acme/engines/deploy.py` |
| `ACME_CUSTOMER_HOST` | Bind address for the Customer runtime's passenger-facing HTTP API (`10.89.1.2` on `web-net` in Compose) | `127.0.0.1` | No | Loopback by default; in Compose binds `web-net` only so `sales-agent` has no network route to port `8800` (#265) | `acme/customer/main.py`, `acme/demo/supervisor.py` |
| `ACME_CUSTOMER_LOOP` | Enable (`!= "0"`) or disable (`"0"`) the 1 s background `CustomerLoop` (OCC poll, scheduled proactive WhatsApps, no-offer closure) | `1` | No | When `0`, background ticking stops; HTTP `GET` endpoints remain strictly read-only and never advance state (#228) | `acme/api/app.py` |
| `ACME_CUSTOMER_PORT` | Port for the Customer runtime's passenger-facing HTTP API | `8800` | No | Non-integer raises `ValueError` at startup | `acme/customer/main.py`, `acme/demo/supervisor.py` |
| `ACME_CUSTOMER_URL` | Base URL of the Customer runtime's A2A listener called by `acme-sales` | `http://127.0.0.1:8802` | No | Sales attaches its bearer token; if pointed at the passenger API, `SalesRouteGuard` rejects every route except `POST /a2a` with `403` | `acme/sales/main.py`, `acme/demo/supervisor.py` |
| `ACME_DEMO_STATIC` | Directory of built frontend static assets served by `acme-demo` | `/app/static` | No | Missing directory skips static SPA mount while still returning `404` on `/api/a2a`, `/api/.well-known`, and `/mcp/*` | `acme/demo/supervisor.py` |
| `ACME_ENABLE_TRACING` | Enable Cloud Trace span export and Cloud Monitoring metric export (`true` or `1`; non-reserved name for Agent Engine, #190) | `""` (disabled; `true` on deployed engines) | No | Disabled unless `true`/`1`; exporter init failures log a warning and never block startup | `acme/telemetry.py`, `acme/engines/deploy.py` (`telemetry.TRACING_ENV` in `metrics.py`) |
| `ACME_GENAI_LOCATION` | Vertex AI Gemini model location (`GOOGLE_CLOUD_LOCATION` is ignored because Agent Engine injects the engine region, #190) | `global` | No | Any value other than `global` raises `LlmConfigError` / `EngineConfigError` and refuses to start | `acme/llm/config.py`, `acme/engines/deploy.py`, `acme/demo/supervisor.py` |
| `ACME_GENAI_PROJECT` | GCP project ID for Vertex AI model calls, Cloud Trace, and Cloud Monitoring (overrides numeric `GOOGLE_CLOUD_PROJECT`, #185/#203) | `acme-airlines-factory` (or `GOOGLE_CLOUD_PROJECT`) | Yes on engines when `LLM_MODE=vertex` (or `GOOGLE_CLOUD_PROJECT`) | Raises `LlmConfigError` / `EngineConfigError` if unset on engine in `vertex` mode or if not `acme-airlines-factory` (`493084803309`) | `acme/llm/config.py`, `acme/engines/customer.py`, `acme/engines/deploy.py`, `acme/telemetry.py`, `acme/metrics.py` |
| `ACME_JOURNEY_DB` | SQLite database path (or `:memory:`) for `SqliteJourneyStore` in `CustomerRuntime` | `:memory:` | No | Invalid or unwritable path raises `sqlite3.OperationalError` on startup/reset | `acme/customer/runtime.py` |
| `ACME_SALES_HOST` | Bind address for the `acme-sales` HTTP server | `127.0.0.1` | No | Loopback by default (`0.0.0.0` in Compose on internal `sim-net` and `a2a-net`) | `acme/sales/main.py`, `acme/demo/supervisor.py` |
| `ACME_SALES_PORT` | Port for the `acme-sales` HTTP server | `8801` | No | Non-integer raises `ValueError` at startup | `acme/sales/main.py`, `acme/demo/supervisor.py` |
| `ACME_SALES_TICK_SECONDS` | Interval in seconds between background `SalesRadar.tick()` cycles in `acme-sales` | `5` | No | Non-float raises `ValueError` at startup; `<= 0` disables the background radar loop | `acme/sales/main.py` |
| `ACME_SALES_TOKEN` | Bearer credential (secret, set at deploy) identifying `sales` to `acme-sim` and fencing Sales to `POST /a2a` on the Customer runtime (#230) | none (secret, set at deploy) | Yes (`acme-sales`, and `acme-customer` when `ENGINE_MODE != "agent-engine"`) | `acme-sales` and `acme-customer` raise `SystemExit` at startup if unset/empty; any Sales-credentialed call outside `POST /a2a` gets `403` and an audit event | `acme/customer/main.py`, `acme/api/a2a_guard.py`, `acme/sales/main.py`, `acme/demo/supervisor.py` |
| `ACME_SIM_HOST` | Bind address for the `acme-sim` MCP simulator server | `127.0.0.1` | No | Loopback by default (`0.0.0.0` in Compose on internal `sim-net` only) | `acme/sim/app.py`, `acme/demo/supervisor.py` |
| `ACME_SIM_PORT` | Port for the `acme-sim` MCP simulator server | `8700` | No | Non-integer raises `ValueError` at startup | `acme/sim/app.py`, `acme/demo/supervisor.py` |
| `ACME_SIM_TOKENS` | Comma-separated `<caller>=<token>` bearer credentials (secret, set at deploy) for `customer`, `sales`, and `test-admin` on `acme-sim` | Random 32-byte per-process tokens (required in `app/compose.yaml`) | Yes in Compose; optional in standalone `acme-sim` | Raises `ValueError` on unknown caller, malformed entry, token `< 12` chars, or duplicate tokens; missing/bad bearer gets `401`, non-allowlisted server gets `403` | `acme/sim/auth.py`, `acme/sim/app.py`, `acme/demo/supervisor.py` |
| `ACME_SIM_URL` | Base URL of `acme-sim` called by `acme-sales` for read-only `inventory` MCP tools | `http://127.0.0.1:8700` | No | Unreachable/unauthorized URL fails radar inventory reads; `acme-sim` denies `sales` on all servers except `inventory` (`403`) | `acme/sales/main.py`, `acme/demo/supervisor.py` |
| `ACME_STATE_DB` | SQLite path for cross-process `SharedState` (`flock` + Pydantic JSON snapshot + Sales sent-set) across workers in one Agent Engine container (#200) | `""` (in-memory per process; `/tmp/acme-state/state.db` on deployed engines) | No | Lock contention past `lock_timeout` raises `LockTimeout`; unwritable/corrupt DB fails the operation (`startup_error` on `set_up`) | `acme/engines/customer.py`, `acme/engines/sales.py`, `acme/engines/deploy.py` |
| `ACME_TELEMETRY_STRICT` | When `"1"`, raise `TelemetryError` on span attributes outside the PII-free allowlist or failing `[A-Za-z0-9_.:-]{1,64}` | `""` (`"1"` in tests) | No | Raises `TelemetryError` when `"1"`; otherwise non-allowlisted or free-text attributes are silently dropped so PII never leaks | `acme/telemetry.py` |
| `ACME_TRACE_SAMPLER` | OpenTelemetry trace sampler on Agent Engine (`parent` restores `ParentBased(ALWAYS_ON)`; anything else uses `ALWAYS_ON`, ADR 0101 / #221) | `""` (`ALWAYS_ON`) | No | Any value other than `parent` defaults to `ALWAYS_ON` so platform head-sampling never drops demo traces | `acme/telemetry.py` |
| `LLM_MODE` | Select LLM backend: `fake` (offline deterministic `FakeLlm`, $0) or `vertex` (`BoundedLlm(VertexLlm)` on global Vertex AI via ADC) | `fake` | No | Any value other than `fake` or `vertex` raises `LlmConfigError` / `EngineConfigError`; HTTP `/scenarios*` presets return `404` outside `fake` | `acme/llm/config.py`, `acme/api/app.py`, `acme/engines/customer.py`, `acme/engines/deploy.py`, `acme/demo/supervisor.py` |
| `LLM_MODEL` | Vertex AI Gemini model ID when `LLM_MODE=vertex` | `gemini-2.5-flash` | No | Empty value falls back to `gemini-2.5-flash`; runtime model errors fall back to deterministic templates or `Other` intent | `acme/llm/config.py` |
| `LLM_TIMEOUT_SECONDS` | Per-call asyncio timeout in seconds for `BoundedLlm` around `VertexLlm` compose and parse calls (#231) | `8.0` | No | Non-numeric or `<= 0` falls back to `8.0`s; on timeout, compose raises `LlmTimeout` (template fallback) and parse fails closed to `Other` (escalates to human, no offer) | `acme/llm/bounded.py` |

Non-`ACME_*`/`LLM_*` runtime variables read alongside the table above: `ENGINE_MODE` (`local` default or `agent-engine`), `AGENT_ENGINE_CUSTOMER` / `AGENT_ENGINE_SALES` / `AGENT_ENGINE_LOCATION` (`us-central1` default) in `acme/api/engine_backend.py` (`503` on first call if invalid/missing), `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY` (platform-injected), `AGENT_ENGINE_SA` / `AGENT_ENGINE_STAGING_BUCKET` / `GITHUB_OUTPUT` in `acme/engines/deploy.py`, `PORT` (`8080` default in `acme/demo/supervisor.py`), and `GOOGLE_API_KEY` / `GEMINI_API_KEY` (refused with `LlmConfigError` if set).

# IRROPS product metrics and proposed alert policies (#235)

The runtime emits OpenTelemetry metrics (`app/backend/src/acme/metrics.py`). Attribute values are
ids/enums only, never passenger data. In deploy, `configure_cloud_monitoring` (called next to
`configure_cloud_trace`, same `ACME_ENABLE_TRACING` switch) exports them to Cloud Monitoring as
`workload.googleapis.com/<name>`. Tests use an in-memory reader.

| Metric | Type | Attributes | Meaning |
|--------|------|------------|---------|
| `acme.guardrail.decisions` | counter | `rule` (reason code, or `all` on approve), `outcome` (approve/reject), `step` | Guardrail decisions; one point per failed rule |
| `acme.offers` | counter | `status` (proposed/accepted/declined) | Offer lifecycle |
| `acme.llm.template_fallbacks` | counter | `surface` (resolution/offer) | LLM text rejected, template used |
| `acme.needs_human` | counter | `specialist` | Escalations to a person |
| `acme.notification.latency` | histogram (s) | none | RESOLVED to `notification_sent` |
| `acme.scenario.rebuild.duration` | histogram (s) | `preset`, `reason` | Scenario rebuild by replay on a container (#280) |
| `acme.http.server.errors` | counter | `status_code` | HTTP 5xx responses from the API |

Export details (#337, #344):

- Each worker process of a container runs its own exporter; every `acme.*` time series therefore
  carries an `opentelemetry_id` label (`add_unique_identifier=True`; without it Cloud Monitoring
  rejects the colliding writes with `InvalidArgument`). **Aggregate across it**: queries and alert
  conditions use `REDUCE_SUM` (counters, histogram counts) or `REDUCE_PERCENTILE_*` over the
  series and must not group by `opentelemetry_id` (the Observatory dashboard queries already group
  only by the labels they show).
- The SDK's own `otel.sdk.*` self-metrics are dropped by a view; only `acme.*` is exported.
- `acme.http.server.errors` is seeded at 0 when the provider is installed, so "no errors" is a
  series of zeros rather than missing data. The Customer engine also counts its own failures (an
  unhandled error in an operation, a 5xx `HTTPException`), as the HTTP middleware only covers the
  API process. The Cloud Run service `acme-irrops-demo` only exports if its service account may
  write metrics (`roles/monitoring.metricWriter`): not granted today (an IAM decision for the PO),
  so its own 5xx counter stays unexported until then.
- `acme.scenario.rebuild.duration` uses sub-second bucket boundaries
  (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.75, 1, 2, 5 s): with the SDK's default ones every rebuild fell in
  the first bucket and the percentiles read 2.5 s / 4.75 s.

## Proposed alert policies (operator creates them; needs infra access)

1. **5xx rate**: `acme.http.server.errors` rate > 0.1/s for 5 min, or any 5xx for 10 min on the
   hosted demo. Severity: page.
2. **needs_human spike**: `acme.needs_human` increase > 3 in 15 min. Severity: ticket.
3. **Collector down**: absence of `acme.notification.latency` or any `acme.*` series for 15 min
   while the demo is up (MQL/PromQL absence condition). Severity: ticket.
4. Optional: template fallback ratio (`acme.llm.template_fallbacks` / notifications) > 50 % in
   `vertex` mode signals a model or prompt regression.
