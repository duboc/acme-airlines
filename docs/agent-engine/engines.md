# Agent Engine apps

Packages under `app/backend/src/acme/engines/`. Platform facts live in
[platform-contract.md](platform-contract.md). This page is the **operations
contract** that the Sales engine (#161) and the demo (#162) build on.

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
| `journey_events(journey_id, since=0) -> list` | sync | Timeline events from index `since` (poll form of the SSE stream). |


Presets are allowed on the engine even with `LLM_MODE=vertex` (the API app still answers 404 outside fake mode). The world is deterministic; only message wording comes from the LLM, and the fact check before sending (`outreach.required_facts`, template fallback) is the same code. Outcomes match local mode structurally (status, flight, seat, offers, charge), not in message text. Guardrails are unchanged.

State is in memory, so a deploy must pin the Customer engine to one instance.

### Environment

| Variable | Default | Rule |
|---|---|---|
| `LLM_MODE` | `fake` | `fake` (dev/CI) or `vertex`; any other value fails at instantiation |
| `GOOGLE_CLOUD_PROJECT` | `acme-airlines-factory` | Must be set explicitly when `LLM_MODE=vertex`. |
| `GOOGLE_CLOUD_LOCATION` | `global` | With `vertex` it **must** be `global`; building fails fast (`EngineConfigError`) otherwise. No API keys, no regional fallback. |

### Deploy helpers

`engine_requirements()`, `engine_extra_packages()` and `DISPLAY_NAME` give the
`requirements=`, `extra_packages=` and display name for the deploy workflow.
The dependency is `google-cloud-aiplatform[adk,agent-engines]>=1.112,<2`: 2.x
needs `google-adk>=1.27` and conflicts with the `fastapi` pin.

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
A step is the Customer `scenario_step`, then one Sales engine `stream_query` turn (its own trace).
Env: `AGENT_ENGINE_CUSTOMER`, `AGENT_ENGINE_SALES` (full resource names; startup fails without
them). CI routes the calls to the local engine apps, and the six presets are structurally equal to
local mode. Bodies are never logged. See `docs/product/deploy.md`.

### Security boundary

Both engines run as `acme-agent-engine` (project-wide `aiplatform.user`), so IAM does not stop the Sales engine from calling the Customer engine's demo ops, which return passenger data. The boundary is enforced in code: `EngineCustomerClient` exposes only the three skills, via `a2a_skill`. The governance architecture test (`tests/governance/test_architecture.py`) checks that Sales sources never call any other Customer engine operation. This is the same posture as local mode, and it is a known, accepted residual risk (security audit of #178).

## Proof

`tests/live/test_agent_engine_traces.py` is an opt-in live check (#167), never run in CI. It
drives presets 1-5 (`ACME_PROOF_PRESETS` overrides) through the **deployed** engines with
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
`pyproject.toml`, `uv.lock`, the workflow) and on `workflow_dispatch`. It has no `pull_request`
trigger, uses WIF only (`OBSERVATORY_WIF_PROVIDER` + `OBSERVATORY_DEPLOYER_SA`) and holds no keys.
Deploys never overlap (`concurrency: agent-engine-deploy`).

`python -m acme.engines.deploy` (run in `app/backend`):

1. Looks up both engines by display name. More than one hit for a name fails the run before anything is written. One hit means update in place; none means create.
2. Deploys `acme-customer-agent`, then `acme-sales-agent` with `ENGINE_MODE=agent-engine` and `ACME_CUSTOMER_ENGINE=<customer resource name>`.
3. Settings: `service_account=$AGENT_ENGINE_SA`, staging bucket and location from `AGENT_ENGINE_*`, env `LLM_MODE=vertex`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION=global`.
4. **Tracing:** this SDK has no `enable_tracing` argument on `create`/`update` for custom templates, and it only wires `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY` into its `AdkApp` template. The deploy therefore sets that env var to `true`, and both engines' `set_up()` call `acme.telemetry.configure_cloud_trace(<display name>)`. It installs an SDK `TracerProvider` with the Cloud Trace exporter (ADC) and `service.name` = the display name only when the var is on and no SDK provider exists yet.
5. **Instances:** the Customer engine is pinned with `min_instances=1`, `max_instances=1`. This is a **demo limitation**: its world is in memory, so state is lost on restart and it does not scale horizontally. Sales is not pinned.

Afterwards the workflow runs `gcloud run services update --update-env-vars` (everything else is kept) to set `AGENT_ENGINES=acme-customer-agent=<id>,acme-sales-agent=<id>` on the `acme-observatory` service and `AGENT_ENGINE_CUSTOMER` / `AGENT_ENGINE_SALES` (full resource names) on `vars.IRROPS_DEMO_SERVICE`. It does not set `ENGINE_MODE` on the demo.

If the first deploy fails on Reasoning Engine service-agent permissions, file a `needs operator: re-run infra/95-agent-engine-platform.sh` issue. Agents never change IAM.

Tests (`tests/engines/test_deploy.py`) use a fake SDK and assert the workflow shape; CI never calls GCP.
