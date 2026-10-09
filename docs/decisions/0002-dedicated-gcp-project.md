# ADR 0002: Dedicated GCP project acme-airlines-factory

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

We want the factory's cost, IAM and blast radius kept separate from other work.

## Decision

New project `acme-airlines-factory` under folder `dubofolder`, on the Argolis billing account, in region `us-central1`, managed through a dedicated gcloud config `acme-factory`.

## Consequences

Clean teardown (delete the project). The org enforces `skipDefaultNetworkCreation`, so bootstrap creates the `default` network.
