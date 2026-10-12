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
7. **`deploy.sh` re-runs reset `settings.yaml`.** **Fix:** re-apply
   `50-web-access.sh` and `55-iap-oauth-client.sh --reapply` after every
   redeploy. Marked blocks make them idempotent.

## Scion specifics

8. **No scripted admin login in IAP proxy mode.** UATs can't create projects
   or secrets, and dev mode would give resources a pseudo-user owner. **Fix:**
   temporary federation trusting gcloud ID tokens for `ORG_DOMAIN`
   (`30-hub-admin-access.sh`); run the CLI on the VM against localhost, and
   always disable afterwards.
9. **Admin access left enabled after a silent script exit.** The hub's restart
   sometimes takes more than 60 seconds. The disable step's `curl … && echo`
   returned non-zero, and `set -e` aborted the caller with its output sent to
   `/dev/null`. **Fix:** wait up to 180 seconds with an explicit error, and
   callers verify `status == disabled` before continuing.
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

## Upstream suggestions for Scion

- **`deploy.sh` zone selection:** a `zone` config field, or fallback on
  stockout.
- **CLI admin login behind IAP:** a supported non-interactive way (for
  example, accept gcloud user tokens as a documented mode).
- **`scion hub secret set`:** read the value from stdin.
- **`gh://` skill URIs:** support nested paths.
