# ADR 0011: Full role catalog and domain-wide web access

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

The product owner asked for the full set of agent roles and for web access
for `@google.com` (and `@duboc.altostrat.com`) users.

## Decision

- **Roles.** Add 8 roles from upstream `scion-frontiers/agent-team@3650f1b`:
  architect, eng-manager, security-auditor, investigator, researcher,
  qa-tester, doc-writer and release-notes. That makes 13 `acme-*` templates.
  - Judgement and security roles run on Claude Opus.
  - Exploratory, research and investigation roles run on Gemini Pro.
  - Docs and release notes run on Gemini Flash.
  - The lead may start one `acme-eng-manager` with `--role full` per
    workstream, with a worker budget. The concurrency cap is 6 agents.
- **Web access** has two layers, applied by `infra/50-web-access.sh`:
  1. IAP `roles/iap.httpsResourceAccessor` on the Cloud Run proxy for
     `domain:google.com` and `domain:duboc.altostrat.com`.
  2. Hub `server.auth`: `user_access_mode: domain_restricted`,
     `authorized_domains: [google.com, duboc.altostrat.com]`,
     `default_user_role: member` (chosen by the product owner).

## Consequences

- **Members can run agents.** Any signed-in member from either domain can
  create projects and start agents on this VM. Those agents use the VM
  service account, which means **Vertex AI spend billed to
  `acme-airlines-factory`**, plus VM capacity. Agents in the acme-airlines
  project also receive the shared `GITHUB_TOKEN`. To narrow this:
  - re-run `infra/50-web-access.sh` with `DEFAULT_USER_ROLE=viewer` (affects
    new users only)
  - demote existing users in Admin → Users
- **External domain and IAP.** `google.com` is outside this project's
  organization. If IAP's Google-managed OAuth client refuses external users,
  they will be blocked at the Google login, before the hub. The fix is a
  custom OAuth client: an External consent screen plus a client ID and secret
  set in the IAP settings for the service. Verify with a real `@google.com`
  login.
- **Re-run after every re-deploy.** `deploy.sh` rewrites `settings.yaml`, so
  `infra/50-web-access.sh` must run again after each re-deploy.
