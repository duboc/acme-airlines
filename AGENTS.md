# AGENTS.md (`CLAUDE.md`) — Project Context & Operating Protocol

This repository holds the **acme-airlines** multi-agent application (`app/`), the **Observatory** evaluation & telemetry dashboard (`observatory/`), and the **Scion software factory** infrastructure (`infra/`, `docs/scion/`), structured after [`duboc/scion-experiment`](https://github.com/duboc/scion-experiment) with two Scion Hub projects:
- **Build Project (`duboc-acme-airlines` on branch `main`):** `lead`, `backend`, `frontend`, `reviewer`, `deployer`
- **Eval Project (`acme-airlines-eval` on orphan branch `eval-dashboard`):** `eval-lead`, `factory-analyst`, `search-evaluator`, `ui-craftsman`, `eval-deployer`

## 1. Build, Lint & Test Commands

Run verification before handing off or committing any change:

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

- **Product spec & stack:** [`docs/product/irrops-use-case.md`](docs/product/irrops-use-case.md), [`docs/product/engines.md`](docs/product/engines.md), [`docs/product/policy-rules.md`](docs/product/policy-rules.md), and [`docs/product/deploy.md`](docs/product/deploy.md).
- **Two Goals, One Guardrail:**
  1. Customer recovery (`acme-customer-agent`) always outranks commercial upsell (`acme-sales-agent`).
  2. Sales has **zero passenger PII** (`journey_status` over A2A returns only `{status, flight, resolution_acknowledged, run_id}`) and can only query read-only `inventory` MCP tools (`403` everywhere else).
  3. `Offer` is strictly typed (regex-validated flight/seat, `Literal`, `Decimal`, `UUID` — no free-text strings) and evaluated by the deterministic guardrail (`acme.policy.guardrail_offer.evaluate`) only when `journey.status == RESOLVED` and `resolution_acknowledged == True`.
- **LLM & Vertex AI:**
  - `LLM_MODE=fake` (default, $0) for all unit, governance, and CI tests.
  - `LLM_MODE=vertex` uses project `acme-airlines-factory` and `ACME_GENAI_LOCATION=global` (never regional endpoints or API keys; ADC only).

## 3. Factory Operating Protocol (`duboc/scion-experiment`)

1. **GitHub is the source of truth:** Issues labeled with the project slug (`duboc-acme-airlines` or `acme-airlines-eval`) and `wave-1`..`wave-5` form the backlog (respecting `Depends on #N`, at most 2 issues in flight). Post plans, commands/results, and deploy/review evidence on the issue.
2. **Only delivered messages count:** Use `scion message <agent> "<from> -> <to>: <text>"` (or `--body-file <file>`) and verify it prints `Message delivered` (or `Message sent to user:<email>.` when escalating to `user:<PO_EMAIL>`).
3. **End every turn with a status:** Always finish your turn with `sciontool status blocked "waiting: <what, from whom>"`, `sciontool status task_completed "<summary>"`, or `sciontool status ask_user "<question>"`.
4. **6-Step Issue Lifecycle (`lead` -> worker -> `reviewer` -> commit slot -> `deployer` -> PO acceptance):**
   - **Plan:** `lead` comments the plan on Issue `#N`.
   - **Build:** `lead` briefs `backend` / `frontend`; worker implements & tests without committing, then messages `lead` (`DONE #N: ...`).
   - **Review:** `lead` checks `git diff` and asks `reviewer`; `reviewer` runs tests, appends to [`REVIEW.md`](REVIEW.md), and messages `lead` (`#N <PASS|FAIL>: <evidence>`).
   - **Commit Slot:** On `PASS`, `lead` grants the commit slot to one agent at a time (`git add <files> && git commit -m "<type>(<scope>): <summary> (#N)" && git push origin <branch>`).
   - **Deploy:** `deployer` exports the pushed SHA via `git archive` to a clean temp dir, deploys `acme-irrops-demo`, verifies live endpoints, appends to [`DEPLOYMENT.md`](DEPLOYMENT.md), and comments on `#N`.
   - **Accept:** `lead` comments the outcome on `#N` and messages the PO (`user:<PO_EMAIL>`). The PO closes the issue to accept.
5. **Trunk isolation:** `main` (`duboc-acme-airlines`) and `eval-dashboard` (`acme-airlines-eval`) are independent orphan trunks. Never merge, rebase, cherry-pick, or open a PR between them.
