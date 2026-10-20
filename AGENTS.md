# AGENTS.md (`CLAUDE.md`) — Project Context & Operating Protocol

This repository holds the **acme-airlines** multi-agent application (`app/`), the **Observatory** evaluation & telemetry dashboard (`observatory/`), and the **Scion software factory** infrastructure (`infra/`, `docs/scion/`), structured after [`duboc/scion-experiment`](https://github.com/duboc/scion-experiment) with two Scion Hub projects:
- **Build Project (`duboc-acme-airlines` on branch `main`):** `lead`, `researcher`, `product-manager`, `backend`, `frontend`, `reviewer`, `deployer`
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
uv run pytest -m budget --no-cov -q -s
cd ../..
python3 .github/scripts/check_action_pins.py
python3 -m unittest discover -s .github/scripts -v

# Clean-venv Agent Engine packaging smoke
uv run python -W ignore -m acme.engines.stage /tmp/staged
uv run python -W ignore scripts/engine_smoke.py /tmp/staged

# Frontend (React, TypeScript, Vite, Vitest)
cd app/frontend
npm ci && npm run lint && npm test && npm run build
npx playwright install --with-deps chromium
npm run e2e
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

1. **GitHub is the source of truth:** Issues labeled with the project slug (`duboc-acme-airlines` or `acme-airlines-eval`) and `wave-1`..`wave-5` form the backlog (respecting `Depends on #N`, distributing up to 4 active `status:in-progress` issues in parallel across the team). Post plans, commands/results, and deploy/review evidence on the issue.
2. **Only delivered messages count:** Use `scion message <agent> "<from> -> <to>: <text>"` (or `--body-file <file>`) and verify it prints `Message delivered` (or `Message sent to user:<email>.` when escalating to `user:<PO_EMAIL>`).
3. **End every turn with a status:** Always finish your turn with `sciontool status blocked "waiting: <what, from whom>"`, `sciontool status task_completed "<summary>"`, or `sciontool status ask_user "<question>"`.
4. **7-Step Specification-Driven Issue Lifecycle (`researcher` -> `product-manager` -> worker -> `reviewer` -> merge -> `deployer` -> `eval-lead` approval & close):**
   - **Research & User Story (`status:triage` -> `status:spec-ready` -> `status:ready`):** For unspecified or newly triaged issues, `lead` dispatches `researcher` to post a `## Research Brief` (root cause, `file:line`, ADR/policy constraints) and `product-manager` to post a `## User Story & Acceptance Contract` (`As a ... I want ... so that ...`, numbered `Given / When / Then` criteria, and a deterministic `Verify:` command block), marking the issue `status:ready`.
   - **Plan:** `lead` comments the execution plan on Issue `#N` and sets `status:in-progress`.
   - **Build (in parallel):** `lead` briefs `backend` (`role:backend`, `bug`, backend `role:qa`), `frontend` (`role:frontend`, `role:docs`), or `reviewer` (`role:security`); worker implements & tests in an isolated git worktree (`/tmp/wt-<N>`), opens a PR (`Refs #N`), and messages `lead` (`DONE #N: ...`).
   - **Review:** `lead` asks `reviewer` to review the PR and run the `Verify:` block; `reviewer` submits a GitHub PR review (`VERDICT: APPROVE` / `VERDICT: REQUEST CHANGES`), appends to [`REVIEW.md`](REVIEW.md), and messages `lead`.
   - **Merge:** On `VERDICT: APPROVE` and green CI, the PR is squash-merged to `main`.
   - **Deploy:** `deployer` exports the merged SHA via `git archive` to a clean temp dir, deploys/verifies `acme-irrops-demo`, appends to [`DEPLOYMENT.md`](DEPLOYMENT.md), and comments on `#N`. `lead` posts the `(merged_sha, cloud_run_revision, verify_output)` tuple, moves `#N` to `status:in-review`, and immediately picks up the next issue.
   - **Eval Acceptance & Dashboard Update:** `eval-lead` (`acme-airlines-eval`) dispatches `search-evaluator` and `factory-analyst` to independently execute the `Verify:` block and `Given / When / Then` criteria against `origin/main` and live `acme-irrops-demo`, approves & closes verified issues (`gh issue close <N>` + `status:done`), files any new live bugs as `duboc-acme-airlines,bug,status:triage`, and dispatches `ui-craftsman` + `eval-deployer` to update and deploy `acme-observatory` on `eval-dashboard`.
5. **Trunk isolation:** `main` (`duboc-acme-airlines`) and `eval-dashboard` (`acme-airlines-eval`) are independent orphan trunks. Never merge, rebase, cherry-pick, or open a PR between them.
