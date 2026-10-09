# ADR 0009: Temporary federation for operator CLI admin access

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

In IAP proxy mode there is no non-interactive CLI login. UATs can't create projects or secrets, and dev mode would give resources a pseudo-user owner.

## Decision

`infra/30-hub-admin-access.sh enable|disable` adds or removes a trusted issuer that accepts gcloud-minted Google ID tokens for `duboc.altostrat.com`. The CLI runs on the VM against localhost. Disable when done.

## Consequences

Admin actions run as the real admin user. While enabled, any domain user with a gcloud token who can reach localhost:8080 (that is, SSH to the VM) could authenticate.
