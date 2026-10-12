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
| `stream_query(message, user_id, session_id)` / `async_stream_query` | stream | One ADK turn of the Customer agent. Yields JSON event dicts. |
| `a2a_skill(skill, args) -> dict` | async | Same dispatch as HTTP `/a2a` (`CustomerSkills.handle_a2a`). Only `list_journeys`, `journey_status` and `propose_offer` are accepted, and offers are validated as structured data. Returns the skill's data, or `{"error": {"code", "message"}}` (the HTTP error object). A rejected call leaves state unchanged. |

State is in memory, so a deploy must pin the Customer engine to one instance.

### Environment

| Variable | Default | Rule |
|---|---|---|
| `LLM_MODE` | `fake` | `fake` (dev/CI) or `vertex` |
| `GOOGLE_CLOUD_PROJECT` | `acme-airlines-factory` | |
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
