# Agent Engine apps

Packages under `app/backend/src/acme/engines/`. This page documents the **operations
contract**, environment configuration, telemetry, and state model for the Customer engine (`#160`),
the Sales engine (`#161`), and `EngineBackend` (`#162`, `#164`) — all shipped and verified in CI and live proof.

## `acme-customer-agent` (`acme.engines.customer`)

`build_customer_engine_app()` returns a custom Agent Engine template over the
unchanged `CustomerRuntime`, `CustomerSkills`, specialists and guardrail. The
mocked enterprise systems run in-process, so the engine makes no network calls
of its own. Call `set_up()` once (the engine does this itself) before use.

| Operation | Mode | Behaviour |
|---|---|---|
| `stream_query(message, user_id, session_id)` / `async_stream_query` | stream | One ADK turn of the Customer agent. Yields JSON event dicts. The sync `stream_query` raises `RuntimeError` inside a running event loop; use `async_stream_query` there. |
| `a2a_skill(skill, args) -> dict` | async | Same dispatch as HTTP `/a2a` (`CustomerSkills.handle_a2a`). Only `list_journeys`, `journey_status` and `propose_offer` are accepted, and offers are validated as structured data. Returns the skill's data, or the JSON-RPC error body exactly as HTTP `/a2a` returns it (`{"jsonrpc", "id", "error": {"code", "message"}}`). A rejected call leaves state unchanged. |
| `scenarios_list() -> list` | sync | The six presets (`PresetView`), as `GET /scenarios`. |
| `scenario_start(scenario_id) -> dict` | sync | Resets the world and seeds the preset; returns `ScenarioState`. Unknown id raises `EngineOperationError`. |
| `scenario_step() -> dict` | async | Customer half of the next scripted step, run as an ADK turn (span attributes `acme.scenario`, `acme.step`). **No Sales tick**: the demo drives Sales through the Sales engine. Returns `ScenarioState`. |
| `scenario_state() -> dict` | sync | Current `ScenarioState`. |
| `journeys_list() -> list`, `journey_detail(journey_id) -> dict` | sync / async | `JourneySummary` / `JourneyDetail`, built by the same functions as the HTTP API. |
| `reset()`, `trigger_cancellation(flight, reason)`, `whatsapp_messages(passenger_id)`, `whatsapp_reply(passenger_id, text)`, `whatsapp_action(passenger_id, action)` | sync / async | The phone panel, reset and trigger, calling the API's own functions (`acme.api.app.do_*`, so offer accept/decline still use `acme.customer.conversion` and its guardrail re-check). API errors come back as `{"error": {"status", "detail"}}` and `EngineBackend` re-raises them as HTTP errors. |
| `journey_events(journey_id, since=0) -> list` | async | Timeline events from index `since` (poll form of the SSE stream; calls `runtime.tick()`, `#395`). |


Presets are allowed on the engine even with `LLM_MODE=vertex` (the API app still answers 404 outside fake mode). The world is deterministic; only message wording comes from the LLM, and the fact check before sending (`outreach.required_facts`, which requires `hotel_id`, `"transfer"`, and `"voucher"` only when those entitlements were granted by `duty_of_care_assessed` under `DOC-05..08`, `#324`, plus template fallback) is the same code. Outcomes match local mode structurally (status, flight, seat, offers, charge), not in message text. Guardrails are unchanged.

State within a container is shared across worker processes via `ACME_STATE_DB` (#200, `max_instances=1`), and calls landing on an unseeded or restarted container rebuild state by deterministic replay and the replay tape ([ADR 0103](adr-0103-stateless-engine-scenarios.md), #266/#279).

### Environment

See the full **[Configuration reference](deploy.md#configuration-reference)** for all 26 `ACME_*` and `LLM_*` variables (including `ACME_SALES_TOKEN`, `ACME_SIM_TOKENS`, and the Compose network split). Engine-relevant variables:

| Variable | Default | Rule / Fail-closed behaviour |
|---|---|---|
| `LLM_MODE` | `fake` | `fake` (dev/CI) or `vertex`; any other value fails at instantiation (`LlmConfigError` / `EngineConfigError`). |
| `LLM_MODEL` | `gemini-2.5-flash` | Gemini model ID for `LLM_MODE=vertex`; empty falls back to `gemini-2.5-flash`. |
| `LLM_TIMEOUT_SECONDS` | `8.0` | Per-call timeout on `BoundedLlm` around `VertexLlm` (#231). Non-numeric or `<= 0` falls back to `8.0`s. On timeout, compose raises `LlmTimeout` (caller sends deterministic template) and parse fails closed to `Other` (no acknowledgement, no offer, escalated to human). |
| `ACME_GENAI_PROJECT` / `GOOGLE_CLOUD_PROJECT` | `acme-airlines-factory` | With `LLM_MODE=vertex` one of them must be set; `ACME_GENAI_PROJECT` wins, else platform-injected `GOOGLE_CLOUD_PROJECT` (`493084803309`). Any other project raises `EngineConfigError`. |
| `ACME_GENAI_LOCATION` | `global` | Model location. With `vertex` it **must** be `global`; building fails fast (`EngineConfigError`) otherwise. No API keys, no regional fallback. `GOOGLE_CLOUD_LOCATION` is never read (#190). |
| `ACME_STATE_DB` | `""` (`/tmp/acme-state/state.db` on deploy) | SQLite path for cross-process `SharedState` (`flock` + Pydantic JSON snapshot + Sales sent-set) across worker processes in one container (#200). Lock contention past `lock_timeout` raises `LockTimeout`. |
| `ACME_ENABLE_TRACING` | `""` (`true` on deploy) | Enables Cloud Trace and Cloud Monitoring export on custom templates (`true`/`1`, #190). Exporter setup errors log a warning and never block startup. |
| `ACME_TRACE_SAMPLER` | `""` (`ALWAYS_ON`) | OpenTelemetry sampler (`parent` restores `ParentBased(ALWAYS_ON)`; anything else records all spans via `ALWAYS_ON`, #221 / ADR 0101). |
| `ACME_TELEMETRY_STRICT` | `""` (`1` in tests) | When `1`, raises `TelemetryError` on span attributes outside the PII-free allowlist; otherwise silently drops them. |
| `ACME_CUSTOMER_LOOP` | `1` | Local HTTP runtime only (`create_local_app`): `0` disables the 1 s background OCC poll/outreach/closure loop while HTTP `GET`s stay read-only (#228). Unused on Agent Engine. |
| `ACME_JOURNEY_DB` | `:memory:` | SQLite database path for `SqliteJourneyStore` in `CustomerRuntime`. |

### Deploy helpers

`engine_requirements()`, `engine_extra_packages()` and `DISPLAY_NAME` give the
`requirements=`, `extra_packages=` and display name for the deploy workflow.
The SDK dependency in `ENGINE_REQUIREMENTS` is pinned to `google-cloud-aiplatform[adk,agent-engines]==1.165.1` (matching `uv.lock`; `pyproject.toml` declares `>=1.112,<2` because 2.x needs `google-adk>=1.27` and conflicts with the `fastapi` pin).

### Run locally

```bash
cd app/backend
LLM_MODE=fake uv run python -c "
import asyncio
from acme.engines.customer import build_customer_engine_app
app = build_customer_engine_app(); app.set_up()
print(asyncio.run(app.a2a_skill('list_journeys', {})))"
uv run pytest tests/engines
```

## Tracing

The agents are deterministic custom `BaseAgent`s, so ADK's auto-tracing (enabled on the
engines with `enable_tracing=True`) does not see the specialists, the guardrail or the Sales
pitch. `acme.telemetry.span()` adds OpenTelemetry spans for them. They behave the same in local
mode (no-op without a tracer provider) and on the engines (exported to Cloud Trace).

| Span | Where | Attributes |
|---|---|---|
| `acme.customer.specialist.<name>` (`irrops_rebooking`, `seat`, `baggage`, `rights`, `concierge`, `change`) | around each specialist invocation | `acme.specialist`, `acme.journey_id` |
| `acme.guardrail.decision` | every offer guardrail evaluation | `acme.journey_id`, `acme.flight`, `acme.step` (`propose`/`send`/`accept`/`recheck`), `acme.guardrail.decision` (`approve`/`reject`), `acme.guardrail.reason_code` (first reason) |
| `acme.a2a.<skill>` (`list_journeys`, `journey_status`, `propose_offer`) | Customer side of `CustomerSkills.handle_a2a` | `acme.skill`, `acme.journey_id` (when present) |
| `acme.sales.pitch` | Sales radar proposal | `acme.journey_id`, `acme.flight` |

### PII-free allowlist

Only these attribute keys are accepted: `acme.scenario`, `acme.journey_id`, `acme.flight`,
`acme.specialist`, `acme.skill`, `acme.guardrail.decision`, `acme.guardrail.reason_code`,
`acme.offer.kind`, `acme.step`. Values must be ids/enums (`[A-Za-z0-9_.:-]{1,64}`), never free
text. Anything else raises `TelemetryError` in strict mode (tests, `ACME_TELEMETRY_STRICT=1`)
and is dropped in production. Never add names, PNRs, phone numbers, emails or message bodies.

Tests: `tests/unit/test_telemetry.py` (allowlist) and
`tests/governance/test_telemetry_spans.py` (spans over the AC1234 story; PII derived from
`acme.domain.fixtures` and the transcripts must not appear in span data or `acme` logs).

## Logging

`acme.logging_setup.configure_logging()` runs in every entrypoint and in the engine's `set_up()`.
It pins third-party loggers (`mcp`, `httpx`, `google_genai`, `google.adk`, ...) to WARNING+ and
masks fixture PII, emails and phone numbers on every log record, so Cloud Logging never gets
passenger data. Test: `tests/unit/test_logging_setup.py`.

## `acme-sales-agent` (`acme.engines.sales`)

`build_sales_engine_app()` wraps the unchanged `SalesAgent` and `SalesRadar`. Inventory comes from an in-process simulator that authenticates as `sales` only. Operation: `async_stream_query` (one radar tick).

**Transport.** Sales' only outbound path is `EngineCustomerClient` (`acme.sales.client`), a `CustomerPort` over the Customer engine's `a2a_skill`. It exposes exactly `list_journeys`, `journey_status` and `propose_offer`, with no generic "call any skill". The Customer side enforces the allowlist and the structured-offer schema, and the guardrail stays in code. The client accepts both error shapes (`{"error": ...}` and a JSON-RPC body).

| Variable | Value |
|---|---|
| `ENGINE_MODE` | `local` (default): `A2ACustomerClient` over HTTP `/a2a`. `agent-engine`: `EngineCustomerClient`. |
| `ACME_CUSTOMER_ENGINE` | Full resource name `projects/<p>/locations/<l>/reasoningEngines/<id>`. Required when `ENGINE_MODE=agent-engine`. |

The governance suites (`tests/governance`) run over **both** transports. Tests use a fake handle that routes to a local Customer engine app, with no GCP.

### Demo in `agent-engine` mode (#164)

`acme.api.engine_backend.EngineBackend` has the scenario and journey interface the API routes use.
Both `scenario_step` and manual `whatsapp_reply` (`#322`) run a Sales engine `stream_query` turn
(`EngineBackend._tick_sales`, its own trace) and refresh the Customer scenario state/tape when a
preset is active. Env: `AGENT_ENGINE_CUSTOMER`, `AGENT_ENGINE_SALES` (full resource names; startup
fails without them). CI routes the calls to the local engine apps, and the six presets are
structurally equal to local mode. Bodies are never logged. See `docs/product/deploy.md`.

### Security boundary

Both engines run as `acme-agent-engine` (`AGENT_ENGINE_SA`, project-wide `aiplatform.user`), and GCP IAM permission `aiplatform.reasoningEngines.query` is resource-scoped per `reasoningEngine` rather than operation-scoped (provisioning separate GCP service accounts per engine requires project IAM grants and is deferred as a PO decision, `#330`). Because `CustomerEngineApp` keeps container-local state in `/tmp/acme-state/state.db` (`max_instances=1`, ADR 0103), `a2a_skill` and the demo operations execute on the same `reasoningEngine` resource. The boundary is enforced in code and verified offline: `EngineCustomerClient` (`acme.sales.client`) exposes and calls only `a2a_skill` (`list_journeys`, `journey_status`, `propose_offer`), `a2a_skill` never returns `_with_tape` or passenger PII, and the governance architecture suite (`tests/governance/test_architecture.py`) verifies that Sales sources never reference or invoke any other Customer engine operation. In local and compose modes the boundary is also enforced at network/listener runtime: Sales is fenced to `POST /a2a` on an A2A-only listener, and a Sales-credentialed request to any other route gets 403 plus an audit event (ADR 0102, #230).

## Proof

`tests/live/test_agent_engine_traces.py` is an opt-in live check (#167), never run in CI. It
drives presets 1-6 (`happy-path`, `declines-upgrade`, `unsatisfiable-request`, `seat-taken`, `hotel-unavailable`, `upgrade-race`; `ACME_PROOF_PRESETS` overrides) through the **deployed** engines with
`EngineBackend`, asserts every step's state is structurally equal to local mode, and queries
Cloud Trace (v1 REST, ADC) for each turn. Per turn it needs the Customer `scenario_step` trace
(root span with `acme.scenario`). Across the run it needs `acme.customer.specialist.*`,
`acme.guardrail.decision`, `acme.a2a.*` and `acme.sales.pitch`. No span name or attribute value
may contain fixture PII. Ingestion lags, so it polls up to 2 minutes per turn. It then prints a
Markdown table (scenario, turn, trace id, console link) to stdout, or to `ACME_PROOF_TABLE`.

```bash
cd app/backend
ACME_LIVE_AGENT_ENGINE=1 AGENT_ENGINE_CUSTOMER=projects/.../reasoningEngines/<id> \
  AGENT_ENGINE_SALES=projects/.../reasoningEngines/<id> \
  uv run pytest tests/live/test_agent_engine_traces.py -m live -s
```

Or run the `workflow_dispatch`-only `agent-engine-live-proof.yml` (WIF as the deployer SA, from
`main`); the table lands in the job summary. The assertion logic has fake-based unit tests
(`tests/live/test_agent_engine_proof_logic.py`) that run in CI without GCP.

## Deploy

`.github/workflows/agent-engine-deploy.yml` runs on push to `main` (paths: `app/backend/src/acme/**`,
`app/backend/pyproject.toml`, `app/backend/uv.lock`, `.github/workflows/agent-engine-deploy.yml`) and on `workflow_dispatch`. It has no `pull_request`
trigger, uses WIF only (`OBSERVATORY_WIF_PROVIDER` + `OBSERVATORY_DEPLOYER_SA`) and holds no keys.
Deploys never overlap (`concurrency: agent-engine-deploy`).

`python -m acme.engines.deploy` (run in `app/backend`):

1. Looks up both engines by display name. More than one hit for a name fails the run before anything is written. One hit means update in place; none means create.
2. Deploys `acme-customer-agent`, then `acme-sales-agent` with `ENGINE_MODE=agent-engine` and `ACME_CUSTOMER_ENGINE=<customer resource name>`.
3. Settings: `service_account=$AGENT_ENGINE_SA`, staging bucket and location from `AGENT_ENGINE_*`, env `LLM_MODE=vertex`, `ACME_GENAI_LOCATION=global`, `ACME_GENAI_PROJECT=acme-airlines-factory`, `ACME_ENABLE_TRACING=true`, `ACME_STATE_DB=/tmp/acme-state/state.db` (no `GOOGLE_CLOUD_*` names: they are reserved, #190).
4. **Tracing and startup (`_set_up()`):** this SDK has no `enable_tracing` argument on `create`/`update` for custom templates, and it only wires `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY` into its `AdkApp` template. The deploy therefore sets our own `ACME_ENABLE_TRACING=true` (a `GOOGLE_CLOUD_*` name is reserved, #190; `configure_cloud_trace` still honours the SDK flag too), and both engines' `_set_up()` call `acme.telemetry.configure_cloud_trace(DISPLAY_NAME)` (which installs an SDK `TracerProvider` with `CloudTraceSpanExporter(project_id=..., resource_regex=r"service\.name")` so `service.name` is preserved on exported spans when tracing is enabled and no SDK provider exists yet, #400), `acme.metrics.configure_cloud_monitoring(DISPLAY_NAME)`, and refresh `self.llm_mode = validate_env()` after unpickling (#440).
5. **Instances:** both `acme-customer-agent` and `acme-sales-agent` are pinned with `min_instances=1`, `max_instances=1` (#200, verified by `verify_started`), because `ACME_STATE_DB` lives on one container's disk. Cross-container calls or restarts are handled by stateless scenario replay and the replay tape ([ADR 0103](adr-0103-stateless-engine-scenarios.md)).

Afterwards the workflow runs `gcloud run services update --update-env-vars` (everything else is kept) to set `AGENT_ENGINES=acme-customer-agent=<id>,acme-sales-agent=<id>` on the `acme-observatory` service and `AGENT_ENGINE_CUSTOMER` / `AGENT_ENGINE_SALES` (full resource names) on `vars.IRROPS_DEMO_SERVICE`. It does not set `ENGINE_MODE` on the demo.

If the first deploy fails on Reasoning Engine service-agent permissions, file a `needs operator: re-run infra/95-agent-engine-platform.sh` issue. Agents never change IAM.

Tests (`tests/engines/test_deploy.py`) use a fake SDK and assert the workflow shape; CI never calls GCP.

## Packaging and the clean-venv smoke test (#185)

The SDK uploads `extra_packages` with `tar.add(path)`, so a path of `src/acme` put the code under
`src/acme/` in `dependencies.tar.gz` and the container had no top-level `acme`: unpickling the engine
failed with `ModuleNotFoundError: No module named 'acme'` and Agent Engine reported "failed to deploy
and cannot serve traffic". `engine_extra_packages()` is therefore `["acme"]` and `deploy.main()` runs
from `package_root()` (the directory that contains `acme/`).

CI job `engine-smoke` guards this offline: `python -m acme.engines.stage DIR` writes the artifacts as
the SDK uploads them (`requirements.txt`, the unpacked tarball, both pickles); a clean virtualenv with
only those requirements (the repo's `acme` is not installed) runs `scripts/engine_smoke.py DIR`: import
`acme` from the tarball, unpickle the Customer engine, `set_up()` it and run a query with
`LLM_MODE=fake`; the Sales engine is loaded (its `set_up()` needs the live Customer engine). Run it
locally the same way. Unit tests (`tests/engines/test_packaging.py`) cover the tarball layout.

## Self-diagnosis and pinned requirements (#185, #330)

Agent Engine injects `GOOGLE_CLOUD_PROJECT` as the project **number** (`493084803309`). The LLM
config accepts the project id or that number for the same project (never another one) and always
hands the client the id; the deploy also sets `ACME_GENAI_PROJECT=acme-airlines-factory`, which wins.

`ENGINE_REQUIREMENTS` pins **23** exact `==` packages — **11** direct and OpenTelemetry packages (`google-cloud-aiplatform[adk,agent-engines]==1.165.1`, `google-adk[a2a]==1.39.1`, `google-genai==2.30.1`, `mcp==1.30.0`, `opentelemetry-api==1.41.1`, `opentelemetry-sdk==1.41.1`, `opentelemetry-exporter-gcp-trace==1.15.0`, `opentelemetry-exporter-gcp-monitoring==1.15.0a0`, `httpx==0.28.1`, `pydantic==2.14.0`, `fastapi==0.141.1`) plus **12** critical runtime transitive packages (`a2a-sdk`, `anyio`, `certifi`, `cloudpickle`, `cryptography`, `grpcio`, `httpcore`, `protobuf`, `pydantic-core`, `starlette`, `urllib3`, `uvicorn`) — matching `uv.lock`
(enforced by `test_engine_requirements_match_uv_lock` and `test_engine_requirements_are_exact_pins_matching_uv_lock`):
the container resolves requirements with `pip`, so unpinned ranges would let container builds drift
from what CI tested.

`set_up()` no longer kills the server: it catches any exception and stores a scrubbed traceback. The
`diagnostics` operation (both engines; **not** an A2A skill, so Sales cannot call it) returns exactly
`{engine, status: ready|failed|starting, startup_error, llm_mode}`. Operations on a failed engine
raise "the engine failed to start; call `diagnostics`". `acme.engines.deploy` calls `diagnostics` on
both engines after create/update and fails the job, printing the error, unless every engine is
`ready`, so a broken engine is never green. `startup_error` is scrubbed by `acme.diagnostics.scrub`:
no local variable values, env values whose name looks secret (TOKEN/KEY/SECRET/PASSWORD/AUTH ...),
credential shapes (Bearer, `ya29.`, `AIza`, PEM, long opaque strings), passenger PII, 4000 chars max.

## Cross-process demo state (#200), part 1: the store

Agent Engine runs **9 agent processes per container** (docs: "The value 9 represents the number of
agent processes running in parallel within each container"; `container_concurrency` only scales
requests per process, and no deployment setting pins one worker). `max_instances=1` pins one
container, not one process, so in-memory scenario/journey state is not shared between turns.

`acme.state` makes it shared through the container's disk (nothing new for the operator):
- `snapshot.capture/restore`: a **pydantic JSON** snapshot of the whole runtime (world, simulator
  state, journey record metadata, cursor, scripted clock, scenario cursor), restored **in place** so
  skills, runners and the scenario runner keep their references. No pickle: the file is data written
  by another process, loading it cannot run code.
- Journey states reuse the `journeys` table of `SqliteJourneyStore` (#16); the snapshot and a version
  counter live in a `meta` table of the same SQLite file (WAL).
- `SharedState.operation(rt)`: take a cross-process `flock`, load the newest snapshot if another
  process wrote one, run, write a new snapshot only if something changed, release. The lock covers the
  whole operation, so the per-journey guarantees of #88 and #97 (one charge, one notification) hold
  across processes. It is reentrant per task/thread, acquired by polling (a cancelled waiter cannot
  leak it) and a wait over `lock_timeout` raises `LockTimeout`.
- Limits, same as the in-memory state it replaces: container-local and lost on restart; **`max_instances=1`
  is mandatory** (a second container would not share the disk). Part 2 wires the engine operations,
  sets `ACME_STATE_DB` and makes the deploy check fail if `max_instances` is not 1.

## Cross-process demo state (#200), part 2: wiring

- Every stateful Customer engine operation (`async_stream_query`, `a2a_skill`, `scenario_start`,
  `scenario_state`, `scenario_step`, `journeys_list`, `journey_detail`, `journey_events`, `reset`,
  `trigger_cancellation`, `whatsapp_*`) is wrapped by `@shared` and runs under
  `SharedState.operation`: lock, load the newest snapshot, run, save. `diagnostics` and
  `scenarios_list` are stateless and unwrapped.
- The deploy sets `ACME_STATE_DB=/tmp/acme-state/state.db` on both engines. Without it (local runs,
  unit tests) operations behave exactly as before and workers are independent.
- The Sales engine shares its only state, the radar's sent-set (JSON blob in the same file), under the
  same kind of lock around the whole tick, so processes never both propose for one journey.
- **`max_instances` must be 1 on both engines** (state is on one container's disk). The deploy pins
  `min_instances=max_instances=1` for both, `acme.engines.deploy.verify_started` fails the job unless
  every engine reports `max_instances == 1` (unreadable also fails). The operator-owned
  `infra/verify.sh engines` should assert `maxInstances == 1` too (requested on #200).
- Tests: two workers alternating through scenario 1-5; four real OS processes tapping Accept at once
  (one charge, one 2A, one seat release: #88 across processes); four processes polling at 22:09
  (one notification: #97 across processes); `engine-smoke` runs two simulated workers over one state
  file in a clean venv.

## Trace export and span attributes (#215)

The live proof saw no `acme.a2a.*` spans and no `acme.scenario` on some Customer step traces (turn 1
of most scenarios, a few later turns), although the structural parity was 100%. The spans were
created (offline, with the real entrypoints, they are exported); the likely cause is export timing:
a `BatchSpanProcessor` exports on a timer, Agent Engine runs several processes per container and can
throttle a process between requests, so the spans of the last operation of a burst stay in that
process's buffer. Fixes:

- **Every engine operation flushes the tracer provider before returning** (`acme.telemetry.flush`,
  bounded 5 s, never raises): the Customer operations (`@shared` wrapper) and the Sales
  `async_stream_query`. Regression tests use a batch processor whose timer never fires.
- **Each scenario turn runs inside a span the code owns**, `acme.scenario.step`, created in the
  `scenario_step` operation with `acme.scenario` and `acme.step` set at creation. The attribute no
  longer depends on `trace.get_current_span()` inside the ADK agent being recording (the agent still
  sets it there too, best effort). `step_trace` in the live proof keeps matching on `acme.scenario`.
- **A2A spans on both sides**: the Customer side keeps its `acme.a2a.<skill>` spans (kind SERVER);
  the Sales clients (`EngineCustomerClient`, `A2ACustomerClient`) now add `acme.a2a.<skill>` spans
  (kind CLIENT) under `acme.sales.pitch`, in the **same trace** as the pitch. No trace context crosses
  the engine boundary (`a2a_skill(skill, args)` carries none, and the allowlisted `args` must stay
  exactly the skill's fields), so the Customer's server span is a separate trace; correlate the two
  by `acme.journey_id` and `acme.skill` (no PII in either).

`REQUIRED_SPANS` in the live proof is unchanged.

## Diagnosing missing traces (#221)

The dump-enabled live proof showed whole Customer-engine unary-call traces (step 0, some polls, the
`propose_offer` handler) that never reach Cloud Trace, including the platform's own server spans.
Evidence first, then the fix. What is in place to find the cause:

- **One structured line per engine operation**, `ACME_DIAG {json}` on stderr (Cloud Logging), for every
  Customer operation and the Sales tick: `op`, `trace_id`, `span_id`, `is_recording`, `sampled`, the
  incoming context (`parent.valid` / `parent.sampled`: an unsampled platform root makes our spans
  non-recording), `flush_ok` / `flush_ms` / `flush_error`, `pid`, `instance`, `provider`. Ids, flags
  and counters only, no free text. Every Customer operation now runs in an owned `acme.engine.op` span
  (`acme.op` = the operation name).
- **Every span export is a line too** (`event: export`, `ok`, `n`, `ms`, `error`): the Cloud Trace
  exporter is wrapped so a failed or raising export (429, auth, network) is surfaced instead of being
  swallowed by the batch processor.
- Reading it: a missing trace whose op line shows `is_recording: false` / `parent.sampled: false` is
  platform sampling; `flush_ok: false` or an `export` line with `ok: false` is our exporter; different
  `pid` / `instance` values per call point to the multi-process or multi-instance hypotheses.

## Live-proof engine diagnostics (#221)

The live proof workflow uploads an engine diagnostics artifact for trace verification
(paired with the per-operation `ACME_DIAG` lines and `acme.engine.op` span):

- **Engine diagnostics artifact** (`engine-logs`, **retention 1 day**): both engines' log lines for the
  run window are read with the deployer's `logging.viewer` and reduced to an **allowlist**: only
  well-formed `ACME_DIAG` lines are kept, and only these fields (each type- and pattern-checked, so
  no free text can pass through a permitted key): `op`, `trace_id`, `span_id`, `is_recording`,
  `sampled`, `parent_valid`, `parent_sampled`, `flush_ok`, `flush_ms`, `flush_error` (a type name),
  `pid`, `instance`, `provider`, and for exports `ok`, `n`, `ms`, `error` (a type name), plus the entry
  timestamp and severity. Every other line (tracebacks, container stderr, request payloads) is dropped
  and only counted (`{"event": "dropped", "lines": n, "errors": m}`); a failed log read is a
  `log_read_failed` marker with the exception type name. The repository is public, so artifacts are
  world-downloadable: that is why it is not a denylist.
- **Unary probe**: workflow input `probe_calls` (N) runs N back-to-back and N/2 spaced (20 s) read-only
  `journeys_list` calls on the Customer engine, then counts the `acme.engine.op` traces (sent vs found,
  lost call indices, per kind) into the job summary. It reports the loss and does not fail the job; the
  probe's own engine diagnostics go to `probe-engine-logs.jsonl` (same allowlist, same artifact).

### Root cause and fix (#221)

The dump-enabled run (38362815809) settled it: **Agent Engine head-samples incoming requests**
(sampled requests were always at least 19.7 s after the previous sampled one, unsampled ones at most
7.1 s: roughly one request per 10-20 s gets through) and our provider, the default
`ParentBased(ALWAYS_ON)`, inherited the decision: 56 of 73 Customer requests arrived with an
unsampled parent (`is_recording=false`), which silenced every span of the request, the platform's own
`POST /api/reasoning_engine` and `fastapi.*` spans included. Step 0 follows `scenario_start` by under a
second (always dropped), the `propose_offer` handler follows a step by under a second, the 1/min poll
rarely lands on a sampling slot. The exporter was healthy (73/73 flushes and 75/75 exports ok).

Fix: `acme.telemetry.build_provider` uses an **ALWAYS_ON sampler**, so every request's spans are
recorded and exported whatever the platform decided (same trace id, so the platform's spans join ours).
It is a demo with a handful of requests, so volume is not a concern. `ACME_TRACE_SAMPLER=parent`
restores the platform's decision (ADR 0101).

## Log redaction (#233)

`acme.logging_setup.configure_logging` installs a redaction backstop on the log record factory, so it
covers every handler. It masks, in the message **and in tracebacks and stack info** (the formatted
exception, including the `__cause__`/`__context__` chain, replaces `exc_info`: exception messages carry
tool payloads on exactly the error paths): the fixture passengers' literals plus patterns for passengers
that are **not** in the fixture: emails, E.164 phone numbers, PNR shape (6 upper-case letters/digits
with at least one of each, excluding flight numbers such as `AC1234`), passenger ids (`P-...`) and
loyalty member ids (`FFP-...`). Names of non-fixture passengers cannot be matched by a pattern, so
passenger free text must still never be logged at all: the operator timeline (SSE `passenger_message`
events) carries it to the console behind IAP only, and a test asserts it never appears in any log
record. `acme.diagnostics.scrub` (engine start-up errors) uses the same `redact`.

## Customer background loop (#228)

The local Customer runtime (`acme-customer`, `create_local_app`) runs its own loop for the lifetime of
the app (`acme.customer.loop.CustomerLoop`, started in the FastAPI lifespan; `ACME_CUSTOMER_LOOP=0`
disables it): every second it ingests new OCC events from the mock stream (`CustomerRuntime.poll`) and
runs `CustomerRuntime.tick()`, which sends each due proactive WhatsApp (22:09 in the scenario) and
advances the #229 no-offer closure, all driven by the runtime's `Clock`. Scenarios 1 and 3 therefore
happen with no API client connected. The loop shares the per-journey and runtime locks with every other
path. A failing iteration is logged (exception type only) and retried with exponential backoff and
jitter (1 s doubling to 30 s, reset by a success); it never ends the loop, and shutdown cancels it
cleanly. **GET endpoints are read-only**: `/whatsapp/{id}/messages` and `/journeys/{id}` no longer tick.
Presets still drive time explicitly (the `notify` step calls `tick()`), and the scenario driver's
`advance_clock` does too.

Agent Engine: there is no background loop (processes are throttled between requests), so the engine's
`journey_detail`, `journey_events`, and `whatsapp_messages` operations call `runtime.tick()` (`#395`);
those polled reads advance time-triggered steps (notifications and closure) by design in that mode.

## Process identity for "no scenario is started" (#266)

The live proof fails with `no scenario is started` at a turn that moves between runs, which points to
the call landing where `scenario_start` did not run (another process, replica or a restarted
container). To confirm it, every engine operation now carries a **non-PII process identity**:
`process_identity()` = `proc` (random id per PID, so forked workers differ), `pid` and `container`
(hash of the kernel boot id and PID 1's start time: the hostname is `localhost` on Agent Engine and
cannot tell replicas or restarts apart). It is in (a) the `ACME_DIAG` op line together with the
scenario this process believes is active (`scenario`, `step`) and the shared-state facts
(`shared_version`, `loaded_version`, `db_inode`), (b) the readable `no scenario is started (...)` error
(ids and counters only), and (c) the `diagnostics` operation (`identity`). The `engine-logs` artifact
allowlist includes the new fields. Reading it: if the failing `scenario_step` line shows a different
`proc`/`container` than the `scenario_start` line and `scenario=None` / `loaded_version` behind
`shared_version`, the state did not reach that process; the same `proc` would disprove the
process-local hypothesis. (Identity is on the log line, the error and `diagnostics`, not on every
response body: several operations return lists or strictly typed models.)

## Stateless scenario ops, replay rebuild & replay tape (#266, #279, #280, #322, ADR 0103)

State is per container (`/tmp/acme-state/state.db`). The caller (`EngineBackend`, and the live proof) sends `preset`, monotonic `step` (`integral()` accepts whole-number protobuf floats like `5.0`), and `tape` on scenario ops and state-dependent reads/phone actions; an engine not at `(preset, step)` — or whose `local_manual` turns do not match `manual_turns(tape)` (`#322`) — rebuilds state via `_ensure_scenario` by deterministic replay of steps `0..step-1` (plus `_replay_manual` turns at each step `0..step`) from the seeded mocks (no spans, no metrics except `acme.scenario.rebuild.duration`) and logs `ACME_DIAG {"event": "rebuild", preset, from_step, to_step, reason, ms, tape_miss, tape_discarded, container...}`. Full decision and trade-offs: [ADR 0103](adr-0103-stateless-engine-scenarios.md).

- **Replay tape (`RecordingLlm` / `TapeLlm`, #279, #322):** On `LLM_MODE=vertex`, message wording and passenger reply parsing come from Gemini, so replaying with `FakeLlm` alone altered message text after a rebuild. `RecordingLlm` records each LLM answer in order (keyed by message kind `offer`/`notice` for compose, or a short SHA-256 of the passenger text for parse) and returns it as `tape` in scenario state. When a manual `whatsapp_reply` or `whatsapp_action` matches the next scripted preset step (`ScenarioRunner.advance_if_matches` / `matches_step`), the preset step cursor advances; otherwise `RecordingLlm` / `TapeLlm` (`acme.llm.tape`) records `manual_reply` / `manual_action` entries (`{m, pid, text|action, step}`) in `tape`, and `_ensure_scenario` replays those manual turns at each step `0..step` via `_replay_manual` (`#322`). `EngineBackend` keeps `tape` next to `preset`/`step`, strips `tape` before returning state to the API client or Sales (the tape holds passenger text and is never logged, traced, exported in metrics, or emitted in `ACME_DIAG`), and sends it back on subsequent Customer calls. During a rebuild, `TapeLlm` replays matching entries and falls back to `FakeLlm` on a miss (`tape_miss`). The tape is untrusted input (at most 32 entries, texts at most 2000 chars, parse and manual entries schema-validated; malformed tapes are ignored as a whole). Because `RecordingLlm` lives per worker process, if replaying with a partial tape raises, the rebuild retries once with no tape (`tape_discarded=1`). Separately, when the live model fails closed to `Other` on a passenger reply, `classify` falls back to the deterministic time pattern (`parse_kind`, `parse_source` in `ACME_DIAG`).
- **Registry cache & rebuild latency budget (#280):** `warm_registry()` in `set_up()` builds simulator tool registries once per process and shares them across runs via `sim/current.py`, while rebuilds reuse sim sessions. This keeps cross-container rebuild p95 within the enforced `< 600 ms` budget (`500 ms` target, checked in CI by `pytest -m budget --no-cov`).
- **Live proof of the rebuild (#279):** The `Agent Engine Live Proof` workflow (`workflow_dispatch` only, never a PR check) has a forced-rebuild step: it runs `happy-path` plainly and snapshots state, journeys and the WhatsApp conversation, parks the engine on `seat-taken`, then asks for the final step of `happy-path`, so the engine rebuilds. The step fails unless (1) the Customer log has exactly one `ACME_DIAG rebuild` for it, (2) the rebuilt state, journeys and conversation (without the random `message_id`; offer message wording may differ when the Sales tick ran on another worker/container as long as position, buttons, attachments, timestamp and offer facts match, reported as `"offer wording differs"`) equal the plain run, and (3) there is no extra or repeated WhatsApp message and no extra offer. The run summary lists the rebuild latency (the engine's `ms` and the client-side wall time). Logic and its offline tests: `app/backend/tests/live/agent_engine_proof.py`, `test_agent_engine_proof_logic.py`.

## Event stream lifetime on Cloud Run (#305)

`GET /journeys/{id}/events` is an SSE stream. On Cloud Run the app is **not told** when a client
disconnects while the response is being proxied (the front proxy, then Google's frontend sit in
between), so the stream cannot rely on a disconnect signal alone. What bounds it:

- a keep-alive comment every 3 s (a write only, never an engine query): a write to a dead
  connection fails and ends the stream, and the front proxy also checks `is_disconnected` every
  second;
- a lifetime cap of **60 s** per stream (`acme.api.sse.SSE_MAX_SECONDS`, was 270 s) with a clean
  end: `EventSource` reconnects by itself, sends `Last-Event-ID`, and the server resumes after that
  event (no duplicates in the UI), so a viewer sees nothing, while a vanished client that the
  platform never reported costs at most a minute of polling;
- the engine is asked at most once per interval per journey however many streams are open (the
  shared poller, 1.6 s backing off to 6 s), and an engine or quota error is a typed 429/503 with
  `Retry-After` (a `retry:` hint inside an open stream).

Expect a reconnect about every minute in the browser network panel: it is by design.
