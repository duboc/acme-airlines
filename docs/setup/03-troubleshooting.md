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
