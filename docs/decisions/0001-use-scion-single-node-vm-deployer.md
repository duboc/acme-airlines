# ADR 0001: Use Scion's supported single-node-vm deployer

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

We need a reproducible Scion hub on one GCE VM, with IAP, NAT, firewall and systemd wiring.

## Decision

Run `scripts/single-node-vm/deploy.sh --config infra/deploy-config.json` headlessly from a clean clone at the release tag. Don't write our own Terraform.

## Consequences

Idempotent and supported upstream, and it handles hardened-org quirks. We inherit its limits: there's no zone option, so we patch one line (ADR 0007).
