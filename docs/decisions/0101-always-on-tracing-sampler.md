# ADR 0101 — ALWAYS_ON telemetry sampler for Agent Engine request tracing

- **Status:** Accepted (2026-10-13)
- **Date:** 2026-10-13
- **Deciders:** acme-lead (approver), acme-backend (author)
- **References:** Issue [#221](https://github.com/duboc/acme-airlines/issues/221), PR [#226](https://github.com/duboc/acme-airlines/pull/226), [`docs/agent-engine/engines.md`](../agent-engine/engines.md)

## Context

The ACME Airlines application deploys reasoning engines on Vertex AI Agent Engine (`acme-customer-agent` and `acme-sales-agent`). The live proof suite (`.github/workflows/agent-engine-live-proof.yml`) runs scripted scenario presets against deployed engines and verifies that governance and operational spans (`acme.scenario`, `acme.step`, `acme.guardrail.decision`, `acme.a2a.*`) appear in Google Cloud Trace.

During live proof execution ([#221](https://github.com/duboc/acme-airlines/issues/221)), entire traces for unary Customer engine calls intermittently failed to reach Cloud Trace. Specifically:
- 100% of turn 1 (step 0) calls across all presets were missing.
- Asynchronous `propose_offer` handler calls were missing.
- Periodic 1-per-minute scenario polls were missing.

Detailed diagnostic instrumentation (PR [#223](https://github.com/duboc/acme-airlines/pull/223), PR [#224](https://github.com/duboc/acme-airlines/pull/224), PR [#225](https://github.com/duboc/acme-airlines/pull/225)) revealed the root cause:
1. **Platform Head-Sampling:** Vertex AI Agent Engine head-samples inbound unary requests at the gateway. Only roughly one request every 10–20 seconds is sampled. Requests arriving <=7.1 seconds after a prior request arrive with `parent.sampled=false` and `is_recording=false`. Because step 0 follows `scenario_start` by under a second, it was consistently unsampled by the platform.
2. **Inherited Sampler Decision:** The application's OpenTelemetry configuration used the default `ParentBased(ALWAYS_ON)` sampler. Under `ParentBased`, when the incoming parent context is unsampled, all downstream child spans (`acme.*`) are silenced as non-recording, preventing export to Cloud Trace.

## Decision

1. **Explicit `ALWAYS_ON` Sampler:**
   In `acme.telemetry.build_provider(service_name, exporter)`, configure the OpenTelemetry `TracerProvider` with `sampler=trace.ALWAYS_ON` by default.
2. **Record Every Request:**
   Every request handled by the engine records and exports application spans to Cloud Trace, irrespective of the platform gateway's sampling decision. Spans retain the platform-provided `trace_id` so that if the platform's own spans (`POST /api/reasoning_engine`) are sampled, application spans attach to them; if the platform root is omitted, the application spans form a complete trace.
3. **Configurable Escape Hatch:**
   Support the environment variable `ACME_TRACE_SAMPLER=parent` to opt back into OpenTelemetry's default `ParentBased(ALWAYS_ON)` sampler if high-volume production deployments require throttling.

## Consequences

- **100% Trace Coverage:** All scenario operations, A2A skill calls, and guardrail decision points are reliably captured in Cloud Trace without intermittent gaps.
- **Deterministic Live Proofs:** The live proof workflow cleanly matches all required traces and assertions without flaky test failures.
- **Negligible Overhead:** For demo and evaluation workloads, trace volume is low, so exporting 100% of spans has negligible cost or performance impact.
