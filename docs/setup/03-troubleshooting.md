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
