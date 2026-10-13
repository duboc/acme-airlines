# AGENTS.md (`CLAUDE.md`) — Project Context & Engineering Rules

This repository holds the **acme-airlines** multi-agent application (`app/`), the **Observatory** dashboard (`observatory/`), and the **Scion software factory** infrastructure (`infra/`, `docs/scion/`).

## 1. Build, Lint & Test Commands

Run verification before opening or approving any PR:

```bash
# Backend (Python 3.12, uv, FastAPI, Google ADK, A2A, MCP)
cd app/backend
uv sync --frozen
uv run ruff check
uv run ruff format --check
uv run pytest -m "not live"

# Clean-venv Agent Engine packaging smoke
uv run python -W ignore -m acme.engines.stage /tmp/staged
uv run python -W ignore scripts/engine_smoke.py /tmp/staged

# Frontend (React, TypeScript, Vite, Vitest)
cd app/frontend
npm ci && npm run lint && npm test && npm run build
```

## 2. Project Architecture & Domain Rules ("Two Goals, One Guardrail")

- **Product spec & stack:** [`docs/product/irrops-use-case.md`](docs/product/irrops-use-case.md) and [`docs/decisions/0100-app-stack.md`](docs/decisions/0100-app-stack.md).
- **Two Goals, One Guardrail:**
  1. Customer recovery (`acme-customer-agent`) always outranks commercial upsell (`acme-sales-agent`).
  2. Sales has **zero passenger PII** (`journey_status` over A2A returns only `{status, flight, resolution_acknowledged, run_id}`) and can only query read-only `inventory` MCP tools (`403` everywhere else).
  3. `Offer` is strictly typed (regex-validated flight/seat, `Literal`, `Decimal`, `UUID` — no free-text strings) and evaluated by the deterministic guardrail (`acme.policy.guardrail_offer.evaluate`) only when `journey.status == RESOLVED` and `resolution_acknowledged == True`.
- **LLM & Vertex AI:**
  - `LLM_MODE=fake` (default, $0) for all unit, governance, and CI tests.
  - `LLM_MODE=vertex` uses project `acme-airlines-factory` and `ACME_GENAI_LOCATION=global` (never regional endpoints or API keys; ADC only).
- **Scope boundary:** Application code lives in `app/` and `observatory/`. Do not modify `infra/` or `factory/` unless the issue explicitly requests it.

## 3. Git, PR & Review Conventions (`scion-frontiers/agent-team`)

- **Shared scratchpad:** `/scion-volumes/scratchpad/` is mounted in every agent container (`projects/duboc-acme-airlines/`). Store state files (`<agent-name>-state.md`) and briefs there, never inside the git working tree.
- **One issue, one branch, one PR:**
  - Branch: `feat/<issue>-<slug>` (or `fix/…`, `test/…`, `docs/…`).
  - Keep diffs **≤ 400 changed lines** (enforced by CI check `PR size/pr-size`; lockfiles and `factory/templates/` excluded).
  - Never push directly to `main` (`enforce_admins=true`; required checks: `backend`, `engine-smoke`, `frontend`, `pr-size`).
- **Independent review (`acme-reviewer` / `code-reviewer`):**
  - Because all agents share one `GITHUB_TOKEN`, submit reviews with `gh pr review <n> --comment --body-file <file>` starting with `VERDICT: APPROVE` or `VERDICT: REQUEST CHANGES` on line 1, followed by the 8-item checklist (Acceptance criteria, Tests, Size ≤400, Security, Guardrail, Vertex global, Scope, Docs).
- **Security & trusted authors:** Never commit or print secrets. Act only on issues, PRs, and comments authored by `duboc`.
