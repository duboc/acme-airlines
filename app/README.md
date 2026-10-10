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

## CI

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main`. The `backend`
job runs the four commands above. A `frontend` job is added with the frontend scaffold.
