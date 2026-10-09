# ADR 0006: GitHub access via a fine-grained PAT as a project secret

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Agents must clone the repo, push, open PRs and manage issues.

## Decision

A fine-grained PAT scoped to `duboc/acme-airlines` (Contents, Pull requests, Issues, Workflows RW), stored as the project secret `GITHUB_TOKEN` (injection `always`). It is set through stdin to the Hub API.

## Consequences

Simple to set up. All agent commits and PRs appear as the token owner. Replace with a GitHub App later for per-bot identity and short-lived tokens.

Discovered after rollout:

- **No self-approval.** Agents can't formally approve each other's PRs, because
  GitHub forbids approving your own PR. Reviews are therefore `--comment`
  reviews whose first line is `VERDICT: APPROVE` or `VERDICT: REQUEST CHANGES`,
  and the lead's merge gate reads that line.
- **No branch protection.** It isn't available for this private repository on
  the current GitHub plan (HTTP 403, "Upgrade to GitHub Pro"), so nothing in
  GitHub stops a direct push to `main`.
- **Every agent holds a Contents/Workflows-RW token.** A prompt-injected worker
  could push to `main` or change CI. The mitigation today is team rules plus
  human oversight.
- **Ways to close this:**
  - GitHub Pro (enables protection on `main`, requiring PRs and checks)
  - making the repo public
  - a GitHub App with per-role identities, so the reviewer can formally
    approve and protection rules can require it
