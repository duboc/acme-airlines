# ADR 0004: Build agent images on the VM

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Agents need core-base, scion-base and harness images.

## Decision

`container_images.source: build`. We also build `scion-claude`, because deploy.sh only builds antigravity.

## Consequences

No registry to manage. Images must be rebuilt after upgrades that change harnesses.
