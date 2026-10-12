# Addendum D: IRROPS agents on Vertex AI Agent Engine; proposal-based backlog

- **Date:** 2026-10-12
- **Status:** Approved by the product owner (2026-10-12 09:36)
- **Extends:** ADR 0100 (the app stack). This changes the IRROPS runtime
  target. It also changes addendum B's idle behaviour.

## Decisions
1. **Runtime.** The IRROPS app's ADK agents run on **Vertex AI Agent Engine**
   in `us-central1`:
   - `acme-customer-agent`: the Customer agent plus its specialists
   - `acme-sales-agent`: the Sales agent. It talks A2A to the Customer agent's
     three PII-free skills only, and the guardrail stays enforced in code.

   The mocked enterprise systems are packaged in process. Model calls stay on
   Vertex **`global`**. (The Agent Engine resource location is regional by
   necessity; that's not a model endpoint.)
2. **Observability.** `enable_tracing=True` sends traces to Cloud Trace (agent
   turns, sub-agent hops, tools, LLM calls). Logs go to Cloud Logging, and
   sessions and traces appear in the Agent Engine console. The Observatory
   gets an Agent Engine panel.
3. **The demo UI and API** (`acme-irrops-demo`, Cloud Run with IAP) call the
   engines through `stream_query`. The demo service account gets
   `aiplatform.user` so it can query them.
4. **LLM mode.** The engines run `LLM_MODE=vertex` (real Gemini, used only when
   the demo is exercised). CI keeps `LLM_MODE=fake`.
5. **Platform (operator):** `infra/95-agent-engine-platform.sh`.
   - staging bucket `gs://acme-airlines-factory-agent-engine`
   - runtime SA `acme-agent-engine` (`aiplatform.user`, `cloudtrace.agent`,
     `logging.logWriter`, `monitoring.metricWriter`)
   - the deployer (WIF) gets `aiplatform.user`, `objectAdmin` on the staging
     bucket, and actAs `acme-agent-engine`
   - the Reasoning Engine service agent gets read access to the bucket and
     token creation on the runtime SA
   - GitHub Actions variables `AGENT_ENGINE_*`
   - check: `verify.sh agent-engine`
   - contract: `docs/agent-engine/platform-contract.md`
6. **Backlog when idle.**
   - The lead files up to about 10 issues labelled `status:proposed`, then
     messages the product owner **once** and waits.
   - Only issues the product owner relabels to `status:triage` (or comments
     "approved" on) are worked.
   - Issues the product owner files directly need no approval.

## Tasks
1. `verify.sh agent-engine` (RED), then the platform script, then GREEN.
2. The contract doc.
3. Update the lead template with the proposal-based backlog, and push it.
4. File the brief issue (product-owner backlog, approved), and notify the lead.
5. ADR 0015 and runbook §9.
