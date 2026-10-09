# ADR 0007: Patch deploy.sh zone selection (DEPLOY_ZONE)

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

deploy.sh always picks the region's first zone. `us-central1-c` was stocked out of n2-standard-16.

## Decision

`infra/10-deploy-hub.sh` applies an idempotent one-line patch to the cached clone so the zone honours `DEPLOY_ZONE` (default `us-central1-a`).

## Consequences

Small local divergence from upstream, worth proposing upstream as a `zone` config field.
