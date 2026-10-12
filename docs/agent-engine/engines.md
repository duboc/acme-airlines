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
