# 03 — Infrastructure Verification & Agent Evaluation Methodology

How to verify the Scion infrastructure (`infra/verify.sh`) and evaluate the ACME Airlines ADK multi-agent system (`app/backend/tests/governance/` + live Vertex AI Agent Engine).

---

## 1. Infrastructure Verification (`infra/verify.sh`)

Run all checks from the repository root:

```bash
bash infra/tests/test_verify.sh   # 37 offline unit tests for verify.sh parsers
bash infra/verify.sh all          # 21 non-destructive live infrastructure assertions
bash infra/verify.sh e2e          # 5 live end-to-end probes (Vertex + Agent Engine + Cloud Trace)
```

| Check Group | Targets in [`infra/verify.sh`](../../infra/verify.sh) | What It Asserts |
|---|---|---|
| **GCP & Hub** | `apis`, `nat`, `vm`, `proxy`, `adminlock`, `webaccess` | Required APIs enabled; Cloud NAT active; `scion-hub-acme` RUNNING; IAP returns `302`; temporary admin federation block absent; IAP IAM + Hub `authorized_domains` + `hub_settings.access` (`origin='seeded'`) match `WEB_DOMAINS`. |
| **Security & Cost** | `agentidentity`, `budget`, `cost`, `telemetry` | Hub uses `default_gcp_identity_mode: assign` with `acme-agent@`; Hub VM SA lacks `roles/iam.serviceAccountAdmin`; `$2,000` budget + `acme-budget-guard` active; all 6 templates enforce model & turn/duration caps; Cloud Monitoring OTel export enabled. |
| **Team & Heartbeat** | `project`, `githubapp`, `team`, `skills`, `loop`, `watchdog` | Project `duboc-acme-airlines` registered; GitHub App enabled; 6 upstream `scion-frontiers/agent-team` templates pushed; 1 persistent coordinator (`acme-lead`) with zero idle standing worker sprawl; `impeccable` skill mounted in `developer`; `acme-factory-watchdog` running hourly (`0 * * * *`), `acme-lead-reincarnate` at `04:00 UTC`, and `acme-factory-tick` at `0 */6 * * *`. |
| **Product & Live E2E** | `observatory`, `agentengine`, `e2e` | `acme-observatory-api` / `acme-observatory-web` Ready; exactly 2 Reasoning Engines (`acme-customer-agent`, `acme-sales-agent`) running as `acme-observatory-api@`; live Claude + Gemini + Agent Engine queries emit `acme.*` spans to Cloud Trace. |

---

## 2. Product Evaluation Methodology (5-Layer Pyramid)

Every PR must pass the deterministic governance suite in [`app/backend/tests/governance/`](../../app/backend/tests/governance/) (`cd app/backend && uv run pytest -q`):

| Layer | Suite | Scenarios | Threshold | What It Evaluates |
|---|---|---|---|---|
| **L1. Trajectory & Tool Sequence** | [`test_eval_harness.py`](../../app/backend/tests/governance/test_eval_harness.py) | 12 | **100%** | Exact tool call order, argument match (`pnr`, `target_flight_id`, `tier`), and required keywords across disruption, voucher, loyalty, and seat flows. |
| **L2. Policy Guardrails ($0 / RBAC)** | [`test_policy_guardrail_eval.py`](../../app/backend/tests/governance/test_policy_guardrail_eval.py) | 15 | **100%** | **Zero false-negative tolerance** on `DISRUPTED`/`CANCELLED` `$0` flight changes (`RULE_DISRUPTED_WAIVER`), refund caps (`<= 50000` cents), voucher overrides (`<= 20000` cents), and desk transfer vs. cabin upgrade boundaries. |
| **L3. Adversarial & Prompt Injection** | [`test_adversarial_redteam.py`](../../app/backend/tests/governance/test_adversarial_redteam.py) | 16 | **100%** | Rejects system-prompt overrides, fake `[SYSTEM: ADMIN_MODE=TRUE]` tags, forged waiver codes, cross-PNR leakage, and negative/excessive amounts; asserts booking + loyalty DB state is unchanged on refusal. |
| **L4. Dual-Desk Routing & Parity** | [`test_agent_eval.py`](../../app/backend/tests/governance/test_agent_eval.py), [`test_agent_engine_parity.py`](../../app/backend/tests/governance/test_agent_engine_parity.py) | 10 + 18 | **>= 85% / 100%** | Desk classification (`customer` vs `sales`), handoff context preservation, and **11-field `STRUCTURAL` parity** between the deterministic state machine and the ADK tool loop (`status`, `booking_status`, `flight_id`, `seat`, `cabin`, `paid_cents`, `refunded_cents`, `voucher_cents`, `miles_delta`, `requires_approval`, `error_code`). |
| **L5. Live Vertex AI Agent Engine + Cloud Trace** | [`agent_engine_proof.py`](../../app/backend/src/acme_Airlines/agent_engine_proof.py) (`verify.sh e2e`) | 2 live engines | **100%** | Invokes the deployed `acme-customer-agent` and `acme-sales-agent` Reasoning Engines on Vertex AI (`gemini-2.5-flash`) with a shared `session_id` and verifies real `acme.session_id`, `acme.agent.desk`, and `acme.tool.name` spans in Cloud Trace. |

```bash
# Run the full offline governance + parity suite (< 5 seconds)
cd app/backend && uv run pytest tests/governance/ -q

# Run the live Vertex AI Agent Engine + Cloud Trace proof
bash infra/verify.sh e2e
```
