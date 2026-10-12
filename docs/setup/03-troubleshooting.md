# Troubleshooting

Problems hit while building the factory, and their fixes.

## `gcloud org-policies list` hangs on a y/N prompt

**Symptom:** bootstrap stalled at `API [orgpolicy.googleapis.com] not enabled
... Would you like to enable and retry? (y/N)`.

**Fix:** enable `orgpolicy.googleapis.com` with the other APIs and export
`CLOUDSDK_CORE_DISABLE_PROMPTS=1` in scripts so gcloud never blocks on input.

## n2-standard-16 stockout in `us-central1-c`

**Symptom:** `deploy.sh` failed at "Creating GCE VM" with
`ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS ... n2-standard-16 ... us-central1-c (reason: stockout)`.
deploy.sh (nightly-20261009) always uses the **first** zone returned by
`gcloud compute zones list --filter=region=...` and has no zone option.

**Fix:** `infra/10-deploy-hub.sh` applies a one-line, idempotent patch to the
cached deploy.sh clone so zone selection honours `DEPLOY_ZONE`
(default `us-central1-a`):

```diff
-ZONE="$(gcloud compute zones list \
+ZONE="${DEPLOY_ZONE:-}"
+[[ -n "$ZONE" ]] || ZONE="$(gcloud compute zones list \
```

Re-running deploy.sh is safe — the firewall rules, service accounts, router and
NAT created on the failed run were reused. Upstream improvement worth filing:
a `zone` config field / zone fallback on stockout in deploy.sh.

## IAP binding: "Cloud Resource Manager API has not been used in project"

**Symptom:** deploy.sh step "Binding IAP access for deployer" logged
`gcloud.iap.web.add-iam-policy-binding ... SERVICE_DISABLED` for
`cloudresourcemanager.googleapis.com`, then fell back to a project-level IAP grant
(which worked).

**Fix:** `cloudresourcemanager.googleapis.com` is now in the bootstrap API list,
so a fresh run binds IAP at the service level as intended.

## `gcloud run deploy --source` 403 on `run-sources-*` bucket

**Symptom:** `493084803309-compute@developer.gserviceaccount.com does not have
storage.objects.get access`. The org disables automatic IAM grants to the
default compute service account, which Cloud Build uses for source deploys.

**Fix:** a dedicated `acme-builder` service account with
`roles/cloudbuild.builds.builder`, passed with `--build-service-account`.

## Budget guard never ran: Pub/Sub push got 403

**Symptom:** the guard logged `403 … lacks run.routes.invoke` for every push.
The VM would **not** have stopped at 100%.

**Cause:** the OIDC token audience defaulted to the push endpoint
(`<url>/`, with a trailing slash), which Cloud Run doesn't accept as the
service audience.

**Fix:** set `--push-auth-token-audience=<service URL>` on the subscription.
This was caught by publishing a below-budget wiring-test message. Always
re-run that test after changing the guard.

## `verify.sh budget` hung

**Cause:** `gcloud billing budgets list` blocked on an interactive
"enable API? (y/N)" prompt.

**Fix:** `infra/lib.sh` now exports `CLOUDSDK_CORE_DISABLE_PROMPTS=1` for every
script.

## `acme-frontend` would not provision: "invalid gh:// URI" (issue #38)

**Symptom:** the hub refused to start `acme-frontend` with
`skill_resolution_failed … invalid gh:// URI … expected gh://owner/repo/skill-name[@ref]`.
The shorthand only maps `gh://owner/repo/<name>` to `skills/<name>` at the repo
root. impeccable's built skill lives at `.claude/skills/impeccable`.
`verify.sh skills` had only checked that the URI string was present, so it
passed.

**Fix:**
- impeccable is vendored as a **template-mounted skill**
  (`factory/templates/acme-frontend/skills/impeccable/`, pinned `d631a88`, with
  Apache-2.0 LICENSE and NOTICE), built by the generator.
- `verify.sh skills` now requires the mounted `SKILL.md` in the hub-stored
  template, and fails on any nested `gh://` URI.

## Repo `.scion/templates` shadowed the hub templates

**Symptom:** the lead noticed that templates committed under `.scion/templates/`
on `main` were treated as project-scope templates inside agent workspaces.
They took precedence over the hub's global `acme-*` templates, and they were
stale.

**Fix:** the generated templates now live in `factory/templates/` (not a Scion
discovery path). The hub's global scope is the single source of truth.

## Scripts exited silently right after minting a hub token

**Symptom:** `70-budget-guard.sh` and `80-observatory-platform.sh` stopped,
with no error, right after `Created version [1] of the secret …`. Once, admin
access was left enabled.

**Cause:** `30-hub-admin-access.sh` waited only 60 seconds for `/healthz`
after restarting the hub. Its final `curl … && echo` returned non-zero when the
restart took longer, and `set -e` aborted the caller, whose output was sent to
`/dev/null`.

**Fix:**
- The wait is now 180 seconds, with an explicit error message.
- Callers no longer silence `disable`, and they verify that `status` reports
  `disabled` before continuing.

## `@google.com` users get IAP "You don't have access"

**Symptom:** `duboc@google.com` signs in to Google, then IAP shows *You don't
have access* on the hub proxy and the Observatory. This happens even though
`domain:google.com` holds `roles/iap.httpsResourceAccessor` on both services.

**Root cause:** `gcloud run deploy --iap` uses IAP's **Google-managed OAuth
client**. Per the Cloud IAP docs, *"Google-managed OAuth clients can only be
used to manage access for internal users that are within an organization. You
must use a custom OAuth configuration to … manage access … for external users
that are outside of your organization."* The project's organization is
`duboc.altostrat.com`, so `google.com` counts as external.

**Fix:** a custom OAuth client with an **External** consent screen in
**production**, applied to both services by `infra/55-iap-oauth-client.sh`
(see runbook §6.3). `infra/verify.sh iap-oauth` fails until it is applied.

## `verify.sh` reported a GitHub variable missing that clearly exists

**Symptom:** `FAIL …: missing: gh-var:AGENT_ENGINE_SA` (and earlier a one-off
`IRROPS_DEMO_SERVICE`), while `gh variable list` showed the variable.

**Root cause:** `gh variable list | grep -q X` under `set -o pipefail`. `grep
-q` exits at the first match, so `gh` gets SIGPIPE (exit 141) and the pipeline
"fails". It got more likely as the number of variables grew. It is not API lag.

**Fix:** capture the output first, then grep a here-string (`gh_var` in
`verify.sh`).

## New service account "does not exist" right after creation

**Symptom:** `add-iam-policy-binding … Service account … does not exist`,
seconds after `service-accounts create`.

**Cause:** IAM eventual consistency.

**Fix:** `retry` with backoff around IAM calls in `95-agent-engine-platform.sh`.

## Agent Engine deploy: 403 on the staging bucket (issue #186)

**Symptom:** the `Agent Engine Deploy` workflow failed before creating any
engine, with 403 Forbidden from Cloud Storage.

**Root cause:** the Vertex AI SDK calls `storage.buckets.get` (bucket metadata)
before uploading engine packages (`_get_gcs_bucket()`). The deployer only had
`roles/storage.objectAdmin` on the bucket, and that role has no bucket-level
permissions. The team diagnosed this correctly.

**Fix:** `roles/storage.legacyBucketReader` (`buckets.get` and list, read-only,
this bucket only) for `acme-observatory-deployer`, codified in
`infra/95-agent-engine-platform.sh`. `verify.sh agent-engine` now asserts it.
The same run also added the deferred Reasoning Engine service-agent token
creation on `acme-agent-engine`.

## Agent Engine deploy: "Environment variable name 'GOOGLE_CLOUD_PROJECT' is reserved" (issue #190)

**Symptom:** after the #186 fix, the deploy got past the upload, then
`create_reasoning_engine` returned 400 FAILED_PRECONDITION.

**Root cause:** Agent Engine reserves and injects the `GOOGLE_CLOUD_*` variables.
The operator's contract had wrongly told the team to set them. Agent Engine's
injected `GOOGLE_CLOUD_LOCATION` is the engine region (`us-central1`). If the
code had derived the Gemini location from it, it would have broken the
global-only rule.

**Fix:** the contract is corrected. The model location now comes from
`ACME_GENAI_LOCATION=global` and is passed explicitly to the client. The
app-code change is #190 (team).

## Observatory panels empty: the collector never succeeded

**Symptom:** the GitHub flow and Agent runs panels were empty for days.
`factory_ops` had no tables.

**Root cause:** every collector execution exited 2:
`Failed to initialize cache at /home/collector/.cache/uv … Permission denied`.
The image creates its user with `--no-create-home` but runs `uv run` at runtime.

**Stopgap (operator):** `gcloud run jobs update acme-observatory-collector
--update-env-vars=UV_CACHE_DIR=/tmp/uv-cache,HOME=/tmp`. The next run wrote 12
agent rows and 1 GitHub row. The GitHub token was also mounted (#141). The
permanent fix and a collector-health indicator are a team p0 issue.

**Lesson:** the dashboard showed "empty" instead of "my pipeline is down".
Health checks have to cover the data pipeline, not just the service.
