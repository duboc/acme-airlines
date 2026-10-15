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
| **Security & Cost** | `identity`, `budget`, `cost`, `telemetry` | Hub uses `default_gcp_identity_mode: assign` with `acme-agent@` (and `acme-observatory-deployer@` for `deployer` / `eval-deployer`); Hub VM SA lacks `roles/iam.serviceAccountAdmin`; `$2,000` budget + `acme-budget-guard` active; all 12 templates match the model mapping and carry no artificial `max_duration` / `max_turns` / `max_model_calls` caps; Cloud Monitoring OTLP export enabled. |
| **Two Projects, 12 Agents & Heartbeats** | `hubproject`, `team`, `lead`, `topology`, `skills`, `loop`, `watchdog` | Both `duboc-acme-airlines` and `acme-airlines-eval` exist on the Hub with `GITHUB_TOKEN` (`injection_mode=always`); all 12 templates (`lead`, `researcher`, `product-manager`, `backend`, `frontend`, `reviewer`, `deployer`, `eval-lead`, `factory-analyst`, `search-evaluator`, `ui-craftsman`, `eval-deployer`) registered; all 12 standing agents active across the 2 projects (`lead` and `eval-lead` containers running); `impeccable` skill mounted in `frontend` and `ui-craftsman`; both 15-minute heartbeat schedules (`duboc-acme-airlines-lead-heartbeat` and `acme-airlines-eval-lead-heartbeat`, `*/15 * * * *`) active; `acme-factory-watchdog` enabled. |
| **Product & Live E2E** | `observatory`, `irrops-demo`, `agent-engine`, `engines`, `traces`, `demo-engine`, `e2e` | `acme-observatory` and `acme-irrops-demo` Ready behind IAP; both Reasoning Engines (`acme-customer-agent`, `acme-sales-agent`) running as `acme-agent-engine@`; live ADK agent spans present in Cloud Trace. |

---

## 2. Application & Gemini Model Evaluation Methodology (4-Boundary Pyramid)

Every change in `duboc-acme-airlines` (and every evaluation run by `search-evaluator` and `factory-analyst` in `acme-airlines-eval`) is verified against the 4 application & Gemini model boundaries:

| Boundary | Code & Test Suites | Threshold | What It Evaluates |
|---|---|---|---|
| **B1. Gemini Model Intent & Fail-Closed Safety** | [`vertex.py`](../../app/backend/src/acme/llm/vertex.py), [`intent.py`](../../app/backend/src/acme/customer/intent.py), [`bounded.py`](../../app/backend/src/acme/llm/bounded.py), [`test_ack_classifier.py`](../../app/backend/tests/unit/test_ack_classifier.py), [`test_parse_fallback.py`](../../app/backend/tests/engines/test_parse_fallback.py) | **0% false-positive `Acknowledge`** | Classifies `<passenger_message>` into `PARSE_SCHEMA` (`departure_after` `HH:MM`, `acknowledge`, `other`) with `8.0s` timeout and regex rescue; fails closed (`Other`) on negation (`"not ok"`), sarcasm, complaint, or prompt injection so Sales A2A upsell never fires prematurely. |
| **B2. Fact-Grounded Composition & Template Fallback** | [`outreach.py`](../../app/backend/src/acme/customer/outreach.py), [`skills.py`](../../app/backend/src/acme/customer/skills.py), [`metrics.py`](../../app/backend/src/acme/metrics.py) | **100% fact retention** | Asserts all 7 `required_facts(facts)` (`cancelled_flight`, `new_flight`, `departure`, `seat`, `hotel_id`, `"transfer"`, `"voucher"`) in LLM-composed WhatsApp messages; tracks `acme.llm.template_fallbacks{surface="resolution"\|"offer"\|"parse_timeout"}` in `IrropsPanel.tsx`. |
| **B3. Deterministic Policy & A2A Guardrails** | [`policy/`](../../app/backend/src/acme/policy/), [`a2a_guard.py`](../../app/backend/src/acme/api/a2a_guard.py), [`tests/policy/`](../../app/backend/tests/policy/), [`tests/governance/`](../../app/backend/tests/governance/) | **100% branch coverage** | ANAC Resolution 400 (1h/2h/4h duty of care), MCT (45m domestic / 90m international), 5-gate `guardrail_offer` (`Two Goals, One Guardrail`), and zero passenger PII over A2A. |
| **B4. 5-Preset `STRUCTURAL` Parity, Replay Tape & Cloud Trace** | [`scenarios.py`](../../app/backend/src/acme/api/scenarios.py), [`engines/customer.py`](../../app/backend/src/acme/engines/customer.py), [`tape.py`](../../app/backend/src/acme/llm/tape.py), [`agent_engine_proof.py`](../../app/backend/tests/live/agent_engine_proof.py) | **100% parity & 0 PII leaks** | Runs all 5 presets (`happy-path`, `declines-upgrade`, `unsatisfiable-request`, `seat-taken`, `hotel-unavailable`) across `local` and live Vertex AI Reasoning Engines (`acme-customer-agent`, `acme-sales-agent`), asserting 11-field `STRUCTURAL` parity, `_ensure_scenario` rebuild p95 `< 600ms`, `REQUIRED_SPANS`, and `pii_failures(traces, fixture_pii()) == []`. |
