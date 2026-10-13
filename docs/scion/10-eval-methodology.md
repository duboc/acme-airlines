# 10. Evaluation methodology

This repository evaluates two things simultaneously:

1. **The multi-agent product ("Two Goals, One Guardrail")** — an autonomous Customer IRROPS recovery agent (`acme-customer-agent`) and a commercial Sales agent (`acme-sales-agent`) running on Vertex AI Agent Engine.
2. **The autonomous Scion software factory** — the 13-role agent team, delivery pipeline, and GCP control plane that builds and operates the product.

Both halves follow a single rule: **deterministic evidence over LLM-as-a-judge prose grading.** Because live Gemini calls (`LLM_MODE=vertex`) vary in phrasing across runs while safety boundaries, money movement, and state transitions must hold 100% of the time, the evaluation harness tests **structural state vectors, formal policy properties, static AST/wire boundaries, deterministic concurrency interleavings, and live Cloud Trace telemetry**.

---

## Part 1: Product & multi-agent evaluation ("Two Goals, One Guardrail")

### 1.1 Evaluation pyramid

| Tier | Suite / entrypoint | Transport & LLM mode | What it proves | When it runs |
|---|---|---|---|---|
| **1. Unit & domain** | `app/backend/tests/{domain,policy,customer,sales,sim,outreach}/` | In-process (`LLM_MODE=fake`) | State machine transitions, MCP tool contracts, outreach required-fact guardrails, deterministic fallback | Every `pytest` run & PR (`CI/backend`) |
| **2. 5-layer governance & adversarial** | `app/backend/tests/governance/` (`conftest.py` parameterizes `["http", "engine"]`) | Both HTTP A2A (`A2ACustomerClient`) and Agent Engine (`EngineCustomerClient`) | Zero-PII boundary, prompt-injection resistance, independent policy oracle (400 Hypothesis cases), state gate, TOCTOU race immunity, required OTel spans | Every `pytest` run & PR (`CI/backend`) |
| **3. Multi-worker shared state** | `app/backend/tests/engines/test_shared_state_wiring.py` | 4 OS processes over SQLite WAL + `flock` | Cross-worker state visibility and single-winner concurrency under Agent Engine multi-container fanout | Every `pytest` run & PR (`CI/backend`) |
| **4. Clean-venv packaging smoke** | `scripts/engine_smoke.py` (`CI/engine-smoke`) | Isolated virtualenv + `dependencies.tar.gz` | Cloudpickle artifacts unpickle and execute `set_up()`, `diagnostics()`, and scenario turns without repo sources on `PYTHONPATH` | Every PR (`CI/engine-smoke`) |
| **5. Browser E2E** | `.github/workflows/e2e.yml` (`app/frontend/e2e/`) | Playwright Chromium + FastAPI | Full UI + BFF scenario progression across presets | Every PR & `main` push (`E2E/playwright`) |
| **6. Live Vertex AI Agent Engine proof** | `app/backend/tests/live/agent_engine_proof.py` (`.github/workflows/agent-engine-live-proof.yml`) | Deployed Vertex AI Reasoning Engines (`LLM_MODE=vertex`, `sampler=ALWAYS_ON`) | 17-turn structural parity across presets 1–5, 100% Cloud Trace span presence, 0 PII in traces/logs | On-demand / release gate (`workflow_dispatch` via WIF) |

---

### 1.2 The 5-layer governance suite (`app/backend/tests/governance/`)

Every governance test runs twice via `@pytest.fixture(params=["http", "engine"])` in `app/backend/tests/governance/conftest.py`: once over the local ASGI A2A HTTP transport and once through the Agent Engine `a2a_skill` dispatch path.

#### Layer 1 — Architecture & zero-PII boundary (`test_architecture.py`)

Proves the Sales agent is structurally incapable of reading passenger PII or mutating customer state:

- **Static AST audit (`engine_op_violations`):** Parses `src/acme/sales/*.py` and `src/acme/engines/sales.py` and asserts that any `.query(op=...)` call targets `op="a2a_skill"` with `skill in {"list_journeys", "journey_status", "propose_offer"}`. Calling `journey_detail`, `whatsapp_inbound`, `scenario_step`, or `diagnostics` from Sales fails the test at AST level.
- **MCP allowlist enforcement:** Connects to all 6 simulated airline MCP servers (`pss`, `dcs`, `loyalty`, `voucher`, `payment`, `inventory`) with `sales` bearer credentials and asserts HTTP `403` on every tool except read-only `inventory/cabin_inventory.get` and `inventory/upgrade_fare.get`.
- **Wire-level zero-PII check:** Calls `journey_status` over A2A after seeding disruption + passenger messages and asserts:
  - Response keys are strictly `{"status", "flight", "resolution_acknowledged", "run_id"}` (where `run_id` is a fresh 12-hex opaque nonce, not a PNR or passenger ID).
  - None of the passenger's sensitive tokens (`Mariana`, `Silva`, `MSLV42`, `P-MARIANA`, `FFP-0001`, `14A`, `BAG0001`, `diamond`) appear anywhere in the serialized payload.

#### Layer 2 — Structured offer schema & prompt-injection fuzzing (`test_structured_offers.py`)

Proves Sales cannot smuggle free-text pitches or prompt injections to the passenger:

- **Schema field inspection:** Introspects `Offer.model_fields` (`src/acme/domain/models.py`) and asserts that **zero** fields accept unconstrained `str`. The only string fields (`flight`, `seat_hint`) carry strict regex patterns (`^AC\d{4}$`, `^\d{1,2}[A-K]$`); all other fields are `Literal`, `UUID`, `Decimal`, or `bool`.
- **Adversarial & Hypothesis fuzzing:** Parameterizes explicit prompt-injection payloads (`"2A\nIgnore previous instructions"`, `"Reply YES now!"`, free-text `TextPart` A2A frames, smuggled JSON keys like `pitch` or `message`) plus 200 Hypothesis-generated arbitrary strings and asserts `ValidationError` or HTTP `400` on every case.

#### Layer 3 — Independent policy oracle & property-based verification (`test_claims_vs_inventory.py`, `test_policy_properties.py`)

Proves the deterministic guardrail (`acme.policy.guardrail_offer.evaluate`) enforces ADR 0100 against authoritative inventory rather than trusting the Sales agent's claims:

- **Claims vs. authoritative inventory (`test_claims_vs_inventory.py`):** Tests 7 adversarial discrepancies where the `Offer` lies about `standard_price` or `net_margin`, prices below `cost_floor`, exceeds the tier recovery cap (`$135` Diamond, `$175` Gold), targets `open_seats == 0`, or targets the wrong flight.
- **400-case Hypothesis oracle (`test_policy_properties.py`):** Generates 400 random `(JourneyState, Offer, CabinInventory)` triples (plus targeted edge mutations) and checks `evaluate()` against an independent 6-rule oracle derived directly from ADR 0100:
  1. `journey.status == RESOLVED`
  2. `resolution_acknowledged is True` and `open_requests == []`
  3. Inventory exists for `(flight, cabin)`, `open_seats > 0`, `price < inventory.standard_price`, and `price <= recovery_cap[tier]`
  4. Recomputed margin `price - inventory.cost_floor >= 0` and equals `offer.net_margin`
  5. `offer.optional is True` and no prior blocking offer or same-flight invalidated offer exists
  6. `offer.origin == "sales"` and `journey.current_flight == offer.flight`
- **Non-vacuity guard:** Asserts `approvals >= 20` across the 400 generated examples so a broken test generator or overly rejective guardrail cannot pass vacuously.

#### Layer 4 — State-machine gate (`test_state.py`)

Drives a journey through `DETECTED → RESOLVING → RESOLVED → REPLANNING → CLOSED` and proves:

- Neither the Sales radar nor a direct `propose_offer` call is approved in `DETECTED`, `RESOLVING`, `REPLANNING`, or `CLOSED`, or while `resolution_acknowledged == False`.
- Reopening a resolved journey (`RESOLVED → REPLANNING`) immediately transitions any active offer (`APPROVED` or `DELIVERED`) to `INVALIDATED_BY_REPLAN` and blocks late acceptance.

#### Layer 5 — Deterministic TOCTOU race injection & concurrency (`test_races.py`)

Uses `flow.interleave(server, tool, callback)` to inject competing state mutations at exact `await` boundaries inside `accept_offer`:

1. **Passenger replans mid-accept:** Injects `"Actually, AC1252 is too late"` right before `inventory/seat.hold` → offer is invalidated, `payment.charge` is never called, net charge is `Decimal("0")`.
2. **Seat stolen before hold:** Another PNR takes `2A` right before `inventory/seat.hold` → returns `CONFLICT`, zero charge.
3. **Cabin sold out before hold:** Remaining seats drop to `0` right before `inventory/seat.hold` → returns `CONFLICT`, zero charge.
4. **DCS seat assignment fails after payment:** `dcs/seat.assign` raises after `payment.charge` succeeds → `CustomerSkills` automatically issues `payment.reverse` and releases the hold, leaving net charge at `Decimal("0")`.
5. **6 concurrent `accept_offer` calls:** Runs `asyncio.gather(*(accept_offer(...) for _ in range(6)))` → exactly 1 succeeds and `payment.charge` is called once.

#### End-to-end outcome & span governance (`test_outcome.py`, `test_telemetry_spans.py`)

- **Outcome invariants (`test_outcome.py`):** Runs the full disruption recovery + commercial story and proves:
  1. No offer is approved before the *final* resolution acknowledgement (`max(ack_times) <= min(approved_times)`).
  2. Declining an offer (`"No thanks"`) followed by 5 radar ticks produces **zero** follow-up offers or messages ("zero pressure").
- **Telemetry span & PII contract (`test_telemetry_spans.py`):** Captures OpenTelemetry spans and `acme.*` log records during a full scenario run and asserts:
  1. Every required span (`acme.customer.specialist.{rebooking,duty_of_care,seating_baggage}`, `acme.guardrail.decision`, `acme.a2a.journey_status`, `acme.a2a.propose_offer`, `acme.sales.pitch`) is emitted with `acme.journey_id` (the non-PII `J-*` identifier).
  2. Zero PII values from `fixture_pii()` (`MSLV42`, `Mariana`, `Silva`, `+55-11-98765-4321`, `P-MARIANA`, `FFP-0001`, transcript messages) appear in any span attribute or log message.

---

### 1.3 Structural parity vs. free-text LLM output

In live mode (`LLM_MODE=vertex`), Gemini composes passenger messages and executes ADK specialist tool calls. We evaluate LLM output on two axes:

1. **Outbound fact guardrail (`app/backend/src/acme/outreach/`):** Before any LLM-drafted message reaches the passenger, `required_facts` checks that mandatory operational facts for that event (e.g., new flight number, seat, hotel, or offer price) are present and accurate; otherwise the deterministic template fallback is used.
2. **11-field structural state vector (`STRUCTURAL` in `app/backend/tests/live/agent_engine_proof.py`):** Across all 17 turns of presets 1–5 (`happy-path`, `declines-upgrade`, `unsatisfiable-request`, `seat-taken`, `hotel-unavailable`), each turn's output is compared against the deterministic baseline on:

   ```python
   STRUCTURAL = (
       "scenario_id", "step_index", "total_steps", "done",
       "journey_status", "flight", "seat",
       "offers_sent", "offer_status", "charged", "needs_human",
   )
   ```

   Free-text wording in `transcript` may vary naturally, while the 11-field structural vector must match 100% on every turn.

---

### 1.4 Live Vertex AI Agent Engine acceptance proof (`agent_engine_proof.py`)

Run via `.github/workflows/agent-engine-live-proof.yml` (`gh workflow run agent-engine-live-proof.yml`) or locally:

```bash
cd app/backend
uv run python -m tests.live.agent_engine_proof \
  --project acme-airlines-factory \
  --location us-central1 \
  --customer-resource-name projects/493084803309/locations/us-central1/reasoningEngines/19782688084852736 \
  --sales-resource-name projects/493084803309/locations/us-central1/reasoningEngines/1985603925432074240 \
  --out /tmp/proof-summary.json \
  --logs-out /tmp/engine-logs.jsonl
```

The proof executes four automated gates against the deployed Reasoning Engines:

1. **Runtime diagnostics (`diagnostics` op):** Asserts both engines run with `llm_mode == "vertex"`, `genai_location == "global"`, `tracing_enabled == True`, and `sampler == "ALWAYS_ON"`.
2. **17-turn structural parity:** Runs presets 1–5 end-to-end against `EngineBackend` (backed by GCS `SharedStateBlob`) and compares `structural(got) == structural(want)` against the in-process reference backend at every turn, plus verifies every op reported `sampled=True` and `is_recording=True`.
3. **Cloud Trace v1 verification (`_wait_for_traces`):** Polls the Cloud Trace REST API over the run window and asserts:
   - At least one `acme.scenario.step` span per executed turn (`>= 17`).
   - Every span in `REQUIRED_SPANS` (`acme.scenario.step`, `acme.customer.specialist.rebooking`, `acme.customer.specialist.duty_of_care`, `acme.customer.specialist.seating_baggage`, `acme.guardrail.decision`, `acme.a2a.journey_status`, `acme.a2a.propose_offer`, `acme.sales.pitch`) is present in Cloud Trace.
4. **Cloud Trace & Cloud Logging zero-PII audit:** Scans every span name and attribute key/value in Cloud Trace against `fixture_pii()` (`pii_hits == []`), and exports only an allowlisted 9-field diagnostic schema (`ACME_DIAG`) to `engine-logs.jsonl`.

---

## Part 2: Autonomous software factory evaluation

### 2.1 Per-PR merge gate

No code reaches `main` (whether authored by an agent or a human operator) without passing two independent gates:

1. **Automated GitHub Actions status checks** (enforced on `main` with `enforce_admins=true` by `infra/35-github-repo.sh`):
   - `CI/backend` — `ruff check`, `ruff format --check`, and `pytest` (unit + 5-layer governance + multi-worker shared-state suites).
   - `CI/engine-smoke` — clean-venv cloudpickle staging & multi-worker execution check (`scripts/engine_smoke.py`).
   - `CI/frontend` — TypeScript build and Vitest suite (`npm test && npm run build`).
   - `PR size/pr-size` — fails if `additions + deletions > 400` unless labeled `size:exception`.
2. **Independent agent code review (`factory/team/roles/reviewer.md`):**
   - Because all agents authenticate to GitHub with one shared token (`GITHUB_TOKEN`), GitHub's native `APPROVE` state cannot separate author from reviewer.
   - Instead, `acme-reviewer` (running on Gemini Flash in its own isolated container and worktree) evaluates every PR against an **8-item checklist** (linked issue + acceptance criteria, TDD tests for new behavior, CI green, ≤400 lines, no secrets/PII, Two-Goals-One-Guardrail intact, no unmocked external calls, scope discipline) and posts a comment whose first line is `VERDICT: APPROVE` or `VERDICT: REQUEST CHANGES`.
   - PRs touching auth, IAM, guardrails, secrets, or CI also require `acme-security-auditor`. `acme-lead` only squash-merges when the latest reviewer verdict is `VERDICT: APPROVE` and all 4 required checks are green.

### 2.2 Platform & E2E verification harness (`infra/verify.sh`)

The factory's infrastructure and runtime state are evaluated by [`infra/verify.sh`](../../infra/verify.sh) (documented in [06-observability.md](06-observability.md)):

- **`bash infra/verify.sh all` (21 platform checks):** Validates GCP APIs, Hub VM health, Vertex model access on `global`, Docker images, templates, cost caps, skill mounts, agent lineage topology, schedule interval, `$2,000` budget guard, Observatory, custom IAP OAuth, Agent Engine IAM, web access domains (`WEB_DOMAINS`), boot-disk snapshot policy, watchdog Cloud Run service + alerts, least-privilege `acme-agent@` identity (`assign` mode), and Cloud Monitoring telemetry export.
- **`bash infra/verify.sh e2e` (5 end-to-end checks):** Validates `lead`, `loop`, deployed `engines`, live ADK/`acme.*` spans in Cloud Trace (`traces`), and `acme-irrops-demo` running in `ENGINE_MODE=agent-engine` (`demo-engine`).
- **Offline parser unit tests (`infra/tests/test_verify.sh`, `infra/tests/test_lib.sh`):** 37 shell unit tests plus Python unit suites for the budget guard (`infra/budget-guard/test_main.py`) and watchdog (`infra/watchdog/test_main.py`) verify every check's failure and success conditions offline.

### 2.3 Continuous operational & cost evaluation

While the factory runs unattended, three systems continuously evaluate its health and efficiency:

1. **Wake-on-change watchdog (`infra/watchdog/`, every hour):** Evaluates GitHub activity (`open_prs`, `open_issues`, `Action runs`) and `acme-lead`'s Hub state (`phase`, `activity`), waking the lead only when GitHub changed, recovering stalled/errored states automatically, reincarnating the lead daily at `04:00 UTC` with a durable handoff, and emitting structured `WATCHDOG_ALERT` logs that trigger a Cloud Monitoring email alert to the Product Owner after 2 consecutive unhealthy runs.
2. **Observatory collector (`app/observatory/`, every 5 min to BigQuery `factory_observability`):** Tracks PR throughput, review rounds (`REQUEST CHANGES` vs. `APPROVE`), CI pass rates, agent activity phases, and per-model token consumption (`scion.usage.tokens`, `gen_ai.api.calls`) vs. the `$2,000/month` budget.
3. **Budget guard (`infra/budget-guard/`):** Evaluates cumulative monthly billing alerts against the `$2,000` threshold and calls `scion suspend --all` at `>= 100%` (stopping token spend without stopping the Hub VM).
