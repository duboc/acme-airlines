#!/usr/bin/env bash
# Addendum C: platform for the Observatory (factory cost and run dashboard). Idempotent.
#
# Creates what the agents cannot (IAM, WIF, IAP, secrets, scheduler):
#   BigQuery     billing_export (Cloud Billing export target), factory_ops (collector output)
#   SAs          acme-observatory           dashboard runtime (read-only)
#                acme-observatory-collector collector runtime (writes factory_ops, reads 2 secrets)
#                acme-observatory-deployer  GitHub Actions deployer via WIF (no keys)
#   WIF          pool github / provider github-actions, repo duboc/acme-airlines on refs/heads/main only
#   AR           docker repo "observatory"
#   Secrets      acme-observatory-hub-token    (hub UAT: agent:list, agent:read; minted here)
#                acme-observatory-github-token (read-only GitHub PAT; added by the product owner)
#   Cloud Run    acme-observatory (placeholder, IAP for WEB_DOMAINS), job acme-observatory-collector
#                (placeholder, Direct VPC egress to the hub)
#   Scheduler    acme-observatory-collect: every 5 min runs the collector job
#   GitHub       Actions variables (non-secret) for the deploy workflow
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"

P="${PROJECT_ID}"; R="${REGION}"; REPO="duboc/acme-airlines"
PNUM="$(gcloud projects describe "${P}" --format='value(projectNumber)')"
WEB_DOMAINS="${WEB_DOMAINS:-google.com duboc.altostrat.com}"
BQ_LOCATION="US"
SA_DASH="acme-observatory@${P}.iam.gserviceaccount.com"
SA_COLL="acme-observatory-collector@${P}.iam.gserviceaccount.com"
SA_DEPL="acme-observatory-deployer@${P}.iam.gserviceaccount.com"
HUB_SECRET="acme-observatory-hub-token"; GH_SECRET="acme-observatory-github-token"
# Hub project UUID: the hub REST API (/api/v1/projects/<id>/agents) rejects the slug "duboc-acme-airlines".
HUB_PROJECT_ID="${HUB_PROJECT_ID:-61b6ae3d-9335-4360-a259-5b306dc229f6}"
PLACEHOLDER_SVC="us-docker.pkg.dev/cloudrun/container/hello"
PLACEHOLDER_JOB="us-docker.pkg.dev/cloudrun/container/job"

log() { printf '\n==> %s\n' "$*"; }
ensure_sa() { gcloud iam service-accounts describe "$1@${P}.iam.gserviceaccount.com" --project="${P}" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$1" --project="${P}" --display-name="$2"; }
proj_role() { gcloud projects add-iam-policy-binding "${P}" --member="serviceAccount:$1" --role="$2" --condition=None --format=none; }
ds_role() {  # ds_role <dataset> <role:READER|WRITER> <sa>
  local tmp; tmp="$(mktemp)"
  bq --project_id="${P}" show --format=prettyjson "${P}:$1" > "${tmp}"
  if ! jq -e --arg e "$3" --arg r "$2" '.access | map(select(.userByEmail==$e and .role==$r)) | length > 0' "${tmp}" >/dev/null; then
    jq --arg e "$3" --arg r "$2" '.access += [{"role":$r,"userByEmail":$e}]' "${tmp}" > "${tmp}.new"
    bq --project_id="${P}" update --source "${tmp}.new" "${P}:$1" >/dev/null
  fi
  rm -f "${tmp}" "${tmp}.new"
}

log "APIs"
gcloud services enable bigquery.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com \
  run.googleapis.com iamcredentials.googleapis.com sts.googleapis.com artifactregistry.googleapis.com \
  monitoring.googleapis.com logging.googleapis.com --project="${P}"

log "BigQuery datasets (${BQ_LOCATION})"
for ds in billing_export factory_ops; do
  bq --project_id="${P}" show --format=none "${P}:${ds}" >/dev/null 2>&1 || \
    bq --project_id="${P}" mk --dataset --location="${BQ_LOCATION}" --description="acme factory ${ds}" "${P}:${ds}"
done

log "Service accounts"
ensure_sa acme-observatory "ACME observatory dashboard (read-only)"
ensure_sa acme-observatory-collector "ACME observatory collector"
ensure_sa acme-observatory-deployer "ACME observatory deployer (GitHub Actions via WIF)"

log "IAM: dashboard (read-only)"
proj_role "${SA_DASH}" roles/bigquery.jobUser
proj_role "${SA_DASH}" roles/monitoring.viewer
proj_role "${SA_DASH}" roles/logging.viewer
ds_role billing_export READER "${SA_DASH}"
ds_role factory_ops READER "${SA_DASH}"
gcloud compute instances add-iam-policy-binding "${VM_NAME}" --zone="$(resolve_zone)" --project="${P}" \
  --member="serviceAccount:${SA_DASH}" --role=roles/compute.viewer --format=none

log "IAM: collector"
proj_role "${SA_COLL}" roles/bigquery.jobUser
ds_role factory_ops WRITER "${SA_COLL}"

log "Secrets"
for s in "${HUB_SECRET}" "${GH_SECRET}"; do
  gcloud secrets describe "${s}" --project="${P}" >/dev/null 2>&1 || \
    gcloud secrets create "${s}" --project="${P}" --replication-policy=automatic
  gcloud secrets add-iam-policy-binding "${s}" --project="${P}" \
    --member="serviceAccount:${SA_COLL}" --role=roles/secretmanager.secretAccessor --format=none
done
if [[ -z "$(gcloud secrets versions list "${HUB_SECRET}" --project="${P}" --filter=state=ENABLED --format='value(name)' | head -1)" || "${ROTATE_TOKEN:-0}" == 1 ]]; then
  bash "${INFRA_DIR}/30-hub-admin-access.sh" enable >/dev/null
  trap 'bash "${INFRA_DIR}/30-hub-admin-access.sh" disable >/dev/null || true' EXIT
  hub_cli hub token create --project duboc-acme-airlines --name acme-observatory-collector \
      --scopes agent:list,agent:read --expires 90d --purpose "observatory collector: read agent state" --json 2>/dev/null \
    | jq -er '.token' | tr -d '\n' | gcloud secrets versions add "${HUB_SECRET}" --project="${P}" --data-file=- --format=none
  # Disable explicitly (not >/dev/null) so a slow hub restart is visible, then verify.
  bash "${INFRA_DIR}/30-hub-admin-access.sh" disable || echo "WARN: disable reported an error; checking status" >&2
  [[ "$(bash "${INFRA_DIR}/30-hub-admin-access.sh" status 2>/dev/null | tail -1)" == "disabled" ]] \
    || { echo "ERROR: admin access still enabled; run: infra/30-hub-admin-access.sh disable" >&2; exit 1; }
  trap - EXIT
fi

log "Artifact Registry: observatory"
gcloud artifacts repositories describe observatory --location="${R}" --project="${P}" >/dev/null 2>&1 || \
  gcloud artifacts repositories create observatory --repository-format=docker --location="${R}" --project="${P}" \
    --description="acme observatory images"

log "Workload Identity Federation: GitHub Actions (${REPO}, main only)"
gcloud iam workload-identity-pools describe github --location=global --project="${P}" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create github --location=global --project="${P}" --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers describe github-actions --workload-identity-pool=github --location=global --project="${P}" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc github-actions --workload-identity-pool=github --location=global \
    --project="${P}" --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository=='${REPO}' && assertion.ref=='refs/heads/main'"
WIF_PROVIDER="projects/${PNUM}/locations/global/workloadIdentityPools/github/providers/github-actions"
gcloud iam service-accounts add-iam-policy-binding "${SA_DEPL}" --project="${P}" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PNUM}/locations/global/workloadIdentityPools/github/attribute.repository/${REPO}" --format=none

log "IAM: deployer (only what deploys need)"
gcloud artifacts repositories add-iam-policy-binding observatory --location="${R}" --project="${P}" \
  --member="serviceAccount:${SA_DEPL}" --role=roles/artifactregistry.writer --format=none
proj_role "${SA_DEPL}" roles/run.developer
for sa in "${SA_DASH}" "${SA_COLL}"; do
  gcloud iam service-accounts add-iam-policy-binding "${sa}" --project="${P}" \
    --member="serviceAccount:${SA_DEPL}" --role=roles/iam.serviceAccountUser --format=none
done

log "Cloud Run: acme-observatory (placeholder, IAP)"
if ! gcloud run services describe acme-observatory --region="${R}" --project="${P}" >/dev/null 2>&1; then
  gcloud beta run deploy acme-observatory --region="${R}" --project="${P}" --image="${PLACEHOLDER_SVC}" \
    --service-account="${SA_DASH}" --no-allow-unauthenticated --iap --max-instances=2 --quiet \
    --set-env-vars="PROJECT_ID=${P},BUDGET_USD=1000,BILLING_DATASET=billing_export,OPS_DATASET=factory_ops"
fi
gcloud run services add-iam-policy-binding acme-observatory --region="${R}" --project="${P}" \
  --member="serviceAccount:service-${PNUM}@gcp-sa-iap.iam.gserviceaccount.com" --role=roles/run.invoker --format=none
read -ra DOMS <<<"${WEB_DOMAINS}"
for d in "${DOMS[@]}"; do
  gcloud iap web add-iam-policy-binding --project="${P}" --resource-type=cloud-run --service=acme-observatory \
    --region="${R}" --member="domain:${d}" --role=roles/iap.httpsResourceAccessor --condition=None --format=none
done

log "Cloud Run job: acme-observatory-collector (placeholder, VPC egress to hub)"
VM_IP="$(gcloud compute instances describe "${VM_NAME}" --zone="$(resolve_zone)" --project="${P}" --format='value(networkInterfaces[0].networkIP)')"
if ! gcloud run jobs describe acme-observatory-collector --region="${R}" --project="${P}" >/dev/null 2>&1; then
  gcloud run jobs deploy acme-observatory-collector --region="${R}" --project="${P}" --image="${PLACEHOLDER_JOB}" \
    --service-account="${SA_COLL}" --network=default --subnet=default --vpc-egress=private-ranges-only \
    --set-env-vars="PROJECT_ID=${P},OPS_DATASET=factory_ops,HUB_ENDPOINT=http://${VM_IP}:8080,HUB_PROJECT=${HUB_PROJECT_ID},GITHUB_REPO=${REPO}" \
    --update-secrets="SCION_HUB_TOKEN=${HUB_SECRET}:latest" \
    --max-retries=1 --task-timeout=300s --quiet
fi
gcloud run jobs add-iam-policy-binding acme-observatory-collector --region="${R}" --project="${P}" \
  --member="serviceAccount:${SA_COLL}" --role=roles/run.invoker --format=none

log "Cloud Scheduler: acme-observatory-collect (*/5)"
if ! gcloud scheduler jobs describe acme-observatory-collect --location="${R}" --project="${P}" >/dev/null 2>&1; then
  gcloud scheduler jobs create http acme-observatory-collect --location="${R}" --project="${P}" \
    --schedule="*/5 * * * *" --time-zone="Etc/UTC" --http-method=POST \
    --uri="https://run.googleapis.com/v2/projects/${P}/locations/${R}/jobs/acme-observatory-collector:run" \
    --oauth-service-account-email="${SA_COLL}"
fi

log "GitHub Actions variables (non-secret)"
gh variable set OBSERVATORY_WIF_PROVIDER -R "${REPO}" --body "${WIF_PROVIDER}"
gh variable set OBSERVATORY_DEPLOYER_SA -R "${REPO}" --body "${SA_DEPL}"
gh variable set OBSERVATORY_PROJECT -R "${REPO}" --body "${P}"
gh variable set OBSERVATORY_REGION -R "${REPO}" --body "${R}"
gh variable set OBSERVATORY_AR_REPO -R "${REPO}" --body "${R}-docker.pkg.dev/${P}/observatory"

URL="$(gcloud run services describe acme-observatory --region="${R}" --project="${P}" --format='value(status.url)')"
log "done — dashboard URL (IAP): ${URL}"
