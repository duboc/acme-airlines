# 03 — Infrastructure Verification & Agent Evaluation Methodology

How to verify the two-project Scion infrastructure (`infra/verify.sh`) and evaluate the ACME Airlines ADK multi-agent system (`app/backend/tests/governance/` + live Vertex AI Agent Engine).

---

## 1. Infrastructure Verification (`infra/verify.sh`)

Run all checks from the repository root:

```bash
bash infra/tests/test_lib.sh      # offline unit tests for infra/lib.sh & factory.local.env
bash infra/tests/test_verify.sh   # offline unit tests for verify.sh parsers
bash infra/verify.sh all          # 21 non-destructive live infrastructure assertions
bash infra/verify.sh e2e          # 5 live end-to-end probes (leads + heartbeats + Agent Engine + Cloud Trace)
```

| Check Group | Targets in [`infra/verify.sh`](../../infra/verify.sh) | What It Asserts |
|---|---|---|
| **GCP & Hub** | `project`, `hub`, `vertex`, `claude-image`, `webaccess`, `iap-oauth`, `backups` | Required APIs enabled; `scion-hub-acme` RUNNING; Vertex AI global endpoint returns HTTP 200 for `gemini-4-argon`, `gemini-3.8-flash`, `claude-opus-5-5`, and `claude-sonnet-5-5`; custom IAP OAuth client + `WEB_DOMAINS` (`viewer` default) active; daily disk snapshots attached. |
| **Security & Cost** | `identity`, `budget`, `cost`, `telemetry` | Hub uses `default_gcp_identity_mode: assign` with `acme-agent@` (and `acme-observatory-deployer@` for `deployer` / `eval-deployer`); Hub VM SA lacks `roles/iam.serviceAccountAdmin`; `$2,000` budget + `acme-budget-guard` active; all 10 templates match the model mapping and non-lead templates enforce `max_duration`, `max_turns`, and `max_model_calls`; Cloud Monitoring OTLP export enabled. |
| **Two Projects, 10 Agents & Heartbeats** | `hubproject`, `team`, `lead`, `topology`, `skills`, `loop`, `watchdog` | Both `duboc-acme-airlines` and `acme-airlines-eval` exist on the Hub with `GITHUB_TOKEN` (`injection_mode=always`); all 10 `scion-experiment` templates (`lead`, `backend`, `frontend`, `reviewer`, `deployer`, `eval-lead`, `factory-analyst`, `search-evaluator`, `ui-craftsman`, `eval-deployer`) registered; all 10 standing agents active across the 2 projects (`lead` and `eval-lead` containers running); `impeccable` skill mounted in `frontend` and `ui-craftsman`; both 15-minute heartbeat schedules (`duboc-acme-airlines-lead-heartbeat` and `acme-airlines-eval-lead-heartbeat`, `*/15 * * * *`) active; `acme-factory-watchdog` enabled. |
| **Product & Live E2E** | `observatory`, `irrops-demo`, `agent-engine`, `engines`, `traces`, `demo-engine`, `e2e` | `acme-observatory` and `acme-irrops-demo` Ready behind IAP; both Reasoning Engines (`acme-customer-agent`, `acme-sales-agent`) running as `acme-agent-engine@`; live ADK agent spans present in Cloud Trace. |

---

## 2. Product Evaluation Methodology (5-Layer Pyramid)

Every change in `duboc-acme-airlines` (and every evaluation run by `search-evaluator` in `acme-airlines-eval`) is verified against the 5-layer evaluation pyramid:

| Layer | Suite | Scenarios | Threshold | What It Evaluates |
|---|---|---|---|---|
| **L1. Trajectory & Tool Sequence** | [`test_eval_harness.py`](../../app/backend/tests/governance/test_eval_harness.py) | 12 | **100%** | Exact tool call order, argument match (`pnr`, `target_flight_id`, `tier`), and required keywords across disruption, voucher, loyalty, and seat flows. |
| **L2. Policy Guardrails ($0 / RBAC)** | [`test_policy_guardrail_eval.py`](../../app/backend/tests/governance/test_policy_guardrail_eval.py) | 15 | **100%** | **Zero false-negative tolerance** on `DISRUPTED`/`CANCELLED` `$0` flight changes (`RULE_DISRUPTED_WAIVER`), refund caps (`<= 50000` cents), voucher overrides (`<= 20000` cents), and desk transfer vs. cabin upgrade boundaries. |
| **L3. Adversarial & Prompt Injection** | [`test_adversarial_redteam.py`](../../app/backend/tests/governance/test_adversarial_redteam.py) | 16 | **100%** | Rejects system-prompt overrides, fake `[SYSTEM: ADMIN_MODE=TRUE]` tags, forged waiver codes, cross-PNR leakage, and negative/excessive amounts; asserts booking + loyalty DB state is unchanged on refusal. |
| **L4. Dual-Desk Routing & Parity** | [`test_agent_eval.py`](../../app/backend/tests/governance/test_agent_eval.py), [`test_agent_engine_parity.py`](../../app/backend/tests/governance/test_agent_engine_parity.py) | 10 + 18 | **>= 85% / 100%** | Desk classification (`customer` vs `sales`), handoff context preservation, and **11-field `STRUCTURAL` parity** between the deterministic state machine and the ADK tool loop. |
| **L5. Live Vertex AI Agent Engine + Cloud Trace** | `infra/verify.sh e2e` | 2 live engines | **100%** | Verifies deployed `acme-customer-agent` and `acme-sales-agent` Reasoning Engines on Vertex AI and real `invoke_agent` / `call_llm` / `execute_tool` / `acme.*` spans in Cloud Trace. |
