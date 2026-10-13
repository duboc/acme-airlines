# 7. Lessons learned

Every non-obvious problem we hit, in roughly the order a new factory will
meet them. Each entry gives the **symptom**, the **root cause**, and the
**fix that is now codified** in a script and usually a `verify.sh` check.
Full write-ups are in [`docs/setup/03-troubleshooting.md`](../setup/03-troubleshooting.md).

## Platform and GCP

1. **Hardened org has no default VPC.** `compute.skipDefaultNetworkCreation`
   is enforced, and Scion's `deploy.sh` needs network and subnet `default`.
   **Fix:** `00-bootstrap-project.sh` creates an auto-mode `default` VPC.
2. **gcloud hangs on "enable API? (y/N)".** **Fix:** export
   `CLOUDSDK_CORE_DISABLE_PROMPTS=1` everywhere (`lib.sh`), and enable
   `orgpolicy` and `cloudresourcemanager` up front.
3. **VM stockout in the first zone.** `deploy.sh` always uses the region's
   first zone. **Fix:** an idempotent one-line patch so the zone honours
   `DEPLOY_ZONE`. (Upstream suggestion: add a `zone` config field.)
4. **Source deploys 403 on the `run-sources-*` bucket.** The org disables
   automatic grants to the default compute SA. **Fix:** a dedicated
   `acme-builder` SA passed with `--build-service-account`.
5. **New SA "does not exist" seconds after creation.** IAM is eventually
   consistent. **Fix:** a `retry` helper around IAM bindings.
6. **IAP "You don't have access" for `@google.com`** even with
   `domain:google.com` bound. Google-managed OAuth clients admit only users
   inside the org. **Fix:** a custom OAuth client (External, In production),
   applied with `55-iap-oauth-client.sh`, and `verify.sh iap-oauth`.
   Details: [09-iap.md](09-iap.md).
7. **`deploy.sh` re-runs reset `settings.yaml`.** **Fix:** re-apply
   `50-web-access.sh` and `55-iap-oauth-client.sh --reapply` after every
   redeploy. Marked blocks make them idempotent.

## Scion specifics

8. **No scripted admin login in IAP proxy mode.** UATs can't create projects
   or secrets, and dev mode would give resources a pseudo-user owner. **Fix:**
   temporary federation trusting gcloud ID tokens for `ORG_DOMAIN`
   (`30-hub-admin-access.sh`); run the CLI on the VM against localhost, and
   always disable afterwards.
9. **Scripts exited silently after their first hub call, and admin access was
   left enabled.** At first we blamed a slow hub restart; that diagnosis was
   **wrong**. The real cause, found in the audit: `hub_cli` piped
   `{ printf token; [[ -n "$HUB_CLI_STDIN" ]] && printf …; }` into SSH. With
   no extra stdin, the brace group returned 1, so under `set -o pipefail`
   every successful `hub_cli` call "failed" and `set -e` aborted the caller
   right after its side effect (a minted token, a created schedule). The
   documented `60-factory.sh pause`/`resume` switch was affected too.
   **Fix:** `if …; then printf …; fi`, plus the regression test
   `infra/tests/test_lib.sh` (stub gcloud, asserts rc 0/0/7). We kept the
   180-second health wait and the `status == disabled` verification.
   *Meta-lesson:* our "fix" passed only because the re-run skipped the
   already-done step. Verify a fix by reproducing the failure first.

10. **`scion hub secret set` can't take a value from stdin.** `@file`
    base64-encodes it into a *file* secret. **Fix:** PUT
    `/api/v1/secrets/<KEY>` with `encoding: raw` from stdin
    (`31-set-project-secret.sh`).
11. **Templates in the product repo's `.scion/templates/` shadow the hub's.**
    Agents' clones resolve project-scope templates first. **Fix:** generate
    them into `factory/templates/` and push to the hub's global scope.
12. **`gh://owner/repo/<path>` skills only resolve `skills/<name>` at the
    root.** Nested paths are rejected at provision time. **Fix:** vendor the
    skill as a *template-mounted* skill. Also make `verify.sh skills` check
    for the real mounted `SKILL.md`, not a string.
13. **The lineage graph only shows existing agents,** and deletions are hard.
    **Fix:** a standing roster, suspended rather than deleted, with ephemeral
    extras for parallelism.
14. **Scheduled messages can't wake a suspended agent.** **Fix:** the lead
    stays running, in `sciontool status blocked` between passes. Workers are
    woken with `scion message --wake`.
15. **Harness/model defaults vs enabled models.** The Claude harness's
    background model (`claude-haiku-4-5`) wasn't enabled. **Fix:** set
    `ANTHROPIC_DEFAULT_HAIKU_MODEL` in the Claude templates, and probe every
    model in `verify.sh vertex`.

## Agents and process

16. **No self-approval on GitHub.** All agents share one identity. **Fix:**
    comment reviews with `VERDICT:` as the first line, and the merge gate
    reads it.
17. **The lead stalls after waiting on an operator.** **Fix:** the operator
    acts on `needs operator` issues promptly, and verifies the lead's
    status-issue timestamp. A direct message unsticks it.
18. **Operator contracts can be wrong.** The Agent Engine contract told agents
    to set `GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION`, which Agent Engine
    reserves. Its injected `GOOGLE_CLOUD_LOCATION` is the engine region, which
    would have silently broken "Gemini on global". **Fix:** correct the
    contract first, then file a p0 code issue. Model location now comes from
    a non-reserved `ACME_GENAI_LOCATION`.
19. **Agents diagnose platform issues well.** For example, #186: the Vertex SDK
    calls `storage.buckets.get`, and `objectAdmin` lacks it. **Practice:**
    verify the diagnosis (`gcloud iam roles describe`), grant the minimal
    role (`storage.legacyBucketReader` on one bucket), codify it, assert it,
    and close with evidence.
20. **Agent-written operator scripts.** Review them before running with
    operator credentials. They were least-privilege and consistent with ours,
    but review is the control.

## Observability and verification

21. **"Empty" ≠ "fine".** The Observatory collector crashed on *every* run for
    days (a `uv` cache permission error, because of `--no-create-home`), so
    the panels were simply empty. **Fix:** a stopgap env first, then running
    the venv interpreter directly, plus a collector-health indicator with a
    freshness threshold. Verify that the job *succeeded*, not just that it was
    triggered.
22. **The budget guard never would have fired.** The Pub/Sub push audience
    defaulted to `<url>/`, and Cloud Run rejected it. **Fix:**
    `--push-auth-token-audience=<url>`, a below-budget wiring test after every
    change, and a self-test job proving the hub path.
23. **A flaky check was a bug in the check.** `gh … | grep -q` under
    `pipefail` fails with SIGPIPE once the list grows. **Fix:** capture first,
    then grep.
24. **Check what users experience, not what config says.** `verify.sh skills`
    passed while the hub refused the skill URI, and `e2e` now checks real
    engines and real agent spans. Every `verify.sh` check got a RED run
    before the fix, and parsers have unit tests (`infra/tests/test_verify.sh`).
25. **Long-lived Opus coordinators + 30-min ticks expire the 5-min prompt
    cache.** Waking a multi-day Claude Opus conversation every 30 minutes
    rewrites the full context into cache ($18.75/MTok) even on no-op passes.
    **Fix:** an external stdlib Python **watchdog** (`infra/76-watchdog.sh`)
    polls GitHub every hour (`0 * * * *`) and wakes `acme-lead` only when issues, PRs,
    or CI runs changed, recovers stalled/errored states automatically, and
    reincarnates `acme-lead` daily at 04:00 UTC with a compact handoff built
    from the pinned status issue. The Hub `TICK` schedule is kept at 6 hours
    as a fallback heartbeat.
26. **Scion's `deploy.sh` grants `roles/iam.serviceAccountAdmin` to the VM SA
    and defaults to `passthrough` metadata.** Any agent container could read
    the VM SA token from metadata and escalate across the project. **Fix:**
    `infra/25-agent-identity.sh` creates a dedicated low-privilege `acme-agent`
    SA, sets `default_gcp_identity_mode: block` (`"hub_sa_minting": false` in
    `deploy-config.json`), assigns `acme-agent` on the project (`assign` mode),
    and removes `roles/iam.serviceAccountAdmin` from the VM SA.
27. **Public repositories amplify prompt injection and untrusted PR risks.**
    With a shared `GITHUB_TOKEN`, untrusted issue/PR text or modified workflows
    can attempt secret exfiltration. **Fix:** `infra/35-github-repo.sh` sets
    `collaborators_only` interaction limits, protects `main` (PR required,
    `enforce_admins=true`, required status checks `test` and `pr-size`), and
    `team-rules.md` Rule #9 forbids fetching or running code from untrusted PRs.
28. **Adding a domain in the GCP IAP Console (`domain:zup.com.br`) still failed
    on the Hub (`Proxy auth: user not authorized`) and on the other two UIs.**
    Adding `domain:<d>` on `scion-hub-acme-iap-proxy` in the Console only opens
    Layer 2 on one service: Scion Hub's own `server.auth.authorized_domains`
    (Layer 4) rejected the user after IAP succeeded, and `acme-observatory` /
    `acme-irrops-demo` still lacked the IAP binding. **Fix:** add the domain to
    `WEB_DOMAINS` in `infra/factory.env` and run `infra/50-web-access.sh`,
    which binds all three IAP Cloud Run services and updates Hub
    `authorized_domains` in one step; `verify.sh webaccess` asserts both.

## Upstream suggestions for Scion

- **`deploy.sh` zone selection:** a `zone` config field, or fallback on
  stockout.
- **CLI admin login behind IAP:** a supported non-interactive way (for
  example, accept gcloud user tokens as a documented mode).
- **`scion hub secret set`:** read the value from stdin.
- **`gh://` skill URIs:** support nested paths.
