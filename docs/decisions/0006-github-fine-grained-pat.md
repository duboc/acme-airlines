# ADR 0006: GitHub access via a fine-grained PAT as a project secret

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Agents must clone the repo, push, open PRs and manage issues.

## Decision

A fine-grained PAT scoped to `duboc/acme-airlines` (Contents, Pull requests, Issues, Workflows RW), stored as the project secret `GITHUB_TOKEN` (injection `always`). It is set through stdin to the Hub API.

## Consequences

Simple to set up. All agent commits and PRs appear as the token owner. Replace with a GitHub App later for per-bot identity and short-lived tokens.
