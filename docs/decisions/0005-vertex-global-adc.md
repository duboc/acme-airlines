# ADR 0005: Vertex AI global endpoint via the VM service account only

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Both Gemini and Claude are needed, with no API keys to leak.

## Decision

Agents use ADC from the metadata server (`default_gcp_identity_mode: passthrough`), with `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION=global`. No regional fallback. Claude models enabled: Opus 5.5, Sonnet 5.5, Haiku 5.5. Claude agents set `ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-5-5`.

## Consequences

One identity for all agents (coarse-grained). Quota is shared on the global endpoint.
