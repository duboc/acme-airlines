# ADR 0014: Observatory, a factory cost and run dashboard built by the factory

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

The product owner wants to see factory spend (mostly tokens), model usage,
agent runs, GitHub flow and budget state in one place. The factory itself
should build and deploy it.

## Decision

- **Where:** a second workstream in the same repo (`observatory/`), under the
  same lead (through an eng-manager).
- **Data sources:** Cloud Billing export → BigQuery, Cloud Monitoring (Vertex
  publisher metrics), and a collector Cloud Run job. Every 5 minutes, the
  collector snapshots the hub API (project UAT `agent:list`/`agent:read`) and
  the GitHub API (read-only PAT) into BigQuery `factory_ops`. Budget panels
  combine billing spend with guard logs.
- **Hosting:** Cloud Run behind IAP (the same domains as the hub). Runtime
  service accounts are read-only (the dashboard) or write only `factory_ops`
  (the collector).
- **CD:** GitHub Actions with keyless Workload Identity Federation, restricted
  to `duboc/acme-airlines@refs/heads/main`. The deployer SA can only deploy
  images to the existing services. The operator owns IAM, WIF, IAP, secrets
  and the scheduler (`infra/80-observatory-platform.sh`); agents own the code
  and workflows.

## Consequences

- The billing panel lags (hours to a day). Run and GitHub panels are near
  real-time.
- Two more 90-day tokens to rotate:
  - the hub UAT: `ROTATE_TOKEN=1 infra/80-observatory-platform.sh`
  - the GitHub read PAT: manual
- Agents merge to main and deploy automatically. A bad merge reaches the
  dashboard within minutes, but it is read-only, so the blast radius is small.
