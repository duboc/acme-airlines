# ADR 0015: IRROPS agents on Vertex AI Agent Engine; proposal-based backlog

- **Status:** Accepted
- **Date:** 2026-10-12
- **Amends:** ADR 0100 (the runtime target) and ADR 0013 (idle mode).

## Context

The product owner wants the agents traced and logged in Google Cloud, so they
can see that everything works. They chose to run the IRROPS ADK agents on
Vertex AI Agent Engine, which has a managed runtime with built-in tracing,
instead of Cloud Run. They also want to approve new backlog before the team
works on it.

## Decision

- **Engines:** `acme-customer-agent` and `acme-sales-agent` on Agent Engine
  (`us-central1`), running as `acme-agent-engine` with `enable_tracing=True`.
  Traces go to Cloud Trace and logs to Cloud Logging. Gemini stays on the
  `global` endpoint, mocks stay in process, and the guardrail stays in code.
- **Demo:** the demo (Cloud Run with IAP) calls the engines. CI never calls
  Vertex or Agent Engine.
- **Platform:** the operator provisions it (`infra/95-agent-engine-platform.sh`,
  `verify.sh agent-engine`). The team deploys keylessly through the existing
  WIF deployer.
- **Idle mode:** the lead proposes at most 10 `status:proposed` issues and asks
  once. Only product-owner-approved issues (relabelled to `status:triage`, or
  commented "approved") are worked.

## Consequences

- Real Gemini spend whenever the hosted demo is exercised. This is covered by
  the $1000 budget and its guard.
- The demo SA gains `aiplatform.user`. The `irrops-demo` check now allows
  exactly that role and nothing else.
- The factory pauses for product-owner input after each backlog completes.
