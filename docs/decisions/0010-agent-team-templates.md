# ADR 0010: Agent team as versioned templates vendored from scion-frontiers/agent-team

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

The product owner wants a normal dev team (lead, backend, frontend, QA, reviewer) orchestrated by a lead agent.

## Decision

Role templates are generated from `factory/team/*` plus upstream agent-team@3650f1b (Apache-2.0) into `factory/templates/acme-*`, then pushed to the hub at global scope. Lead: Claude Opus with `--role full`. Workers: `--role baseline`. GitHub Issues hold the backlog; the lead merges after reviewer approval and green CI.

## Consequences

Mature upstream process guidance. We must re-run the generator to pick up upstream changes. Cross-vendor review (Gemini code reviewed by Claude).
