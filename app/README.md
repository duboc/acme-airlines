# ACME Airlines application

- `backend/` — Python 3.12 uv project `acme` (agents, MCP sim servers, policy code).

## Backend: lint and test locally

Prerequisite: [uv](https://docs.astral.sh/uv/).

```bash
cd app/backend
uv sync --frozen                 # install pinned deps (uv.lock)
uv run ruff check                # lint
uv run ruff format --check       # format check (use `uv run ruff format` to fix)
uv run pytest -m "not live"      # tests; `live` tests are deselected
```

Tests marked `@pytest.mark.live` call real services (e.g. Vertex AI) and never run by
default or in CI. Run them explicitly with `uv run pytest -m live`.

## LLM layer (`acme.llm`)

Agents depend on `LlmPort` (`compose_passenger_message`, `parse_passenger_request`) and get it
from `acme.llm.build_llm()`.

| Env var | Meaning |
|---------|---------|
| `LLM_MODE` | `fake` (default; deterministic, offline, used by dev/tests/CI) or `vertex` (live demo only). Anything else refuses to start. |
| `LLM_MODEL` | Optional model ID for `vertex`. Default `gemini-2.5-flash` (confirmed on the `global` endpoint from the factory VM; `gemini-2.0-flash` is not available there). |
| `GOOGLE_CLOUD_PROJECT` | Optional; must be `acme-airlines-factory` if set. |
| `GOOGLE_CLOUD_LOCATION` | Optional; must be `global` if set. Regional locations are refused. |

Vertex uses ADC only. Setting `GOOGLE_API_KEY` or `GEMINI_API_KEY` refuses to start. The hygiene
test `tests/unit/test_no_keys_no_regions.py` fails on API keys, regional endpoints or non-global
locations anywhere in `app/`.

Live smoke test (factory VM with ADC):

```bash
cd app/backend
LLM_MODE=vertex uv run pytest -m live tests/live
```

## Runtime API (`acme.api`)

FastAPI REST + SSE for the UI. Agent behaviour is stubbed (#17); the Customer agent work (#10-#16)
wires in real agents without changing the contract.

```bash
cd app/backend
uv run uvicorn acme.api.main:app --reload      # http://localhost:8000/docs
uv run python -m acme.api.openapi              # regenerate the committed contract
```

The contract is committed at `app/backend/openapi.json`; a test fails if `GET /openapi.json`
drifts from it, so regenerate and commit it whenever endpoints or models change. CORS allows the
Vite dev server (`http://localhost:5173`). `GET /journeys/{id}/events` is Server-Sent Events: one
`data:` frame (JSON `JourneyEvent`) per timeline event, replaying history first; optional
`?limit=N` closes the stream after N events.

## CI

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main`. The `backend`
job runs the four commands above. A `frontend` job is added with the frontend scaffold.
