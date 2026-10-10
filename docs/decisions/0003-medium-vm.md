# ADR 0003: n2-standard-16 VM with a 500 GB disk

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Several concurrent agents plus on-VM image builds.

## Decision

deploy.sh `machine_size: medium`, `disk_size_gb: 500`.

## Consequences

About $570/month, running 24/7 by product-owner rule (cost control means pausing agents, ADR 0013). Can hit zone stockouts (ADR 0007).
