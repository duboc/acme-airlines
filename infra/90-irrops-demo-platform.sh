#!/usr/bin/env bash
# Demo platform for the IRROPS MVP: ONE Cloud Run service behind IAP, mocked scenarios only. Idempotent.
#
# The operator runs this once (the Observatory deployer has only run.developer, so it cannot create
# an IAP service or IAM bindings):
#   infra/90-irrops-demo-platform.sh
#
# Creates (nothing else is granted):
#   Artifact Registry   docker repo "irrops" (images pushed later by acme-observatory-deployer)
#   Service account     acme-irrops-demo, NO project roles (LLM_MODE=fake needs no GCP access)
#   Cloud Run service   acme-irrops-demo: placeholder image, --no-allow-unauthenticated --iap,
#                       --max-instances=1 (the demo state is in memory), LLM_MODE=fake
#   IAM                 IAP service agent -> run.invoker on the service
#                       domains in WEB_DOMAINS (default google.com duboc.altostrat.com)
#                         -> iap.httpsResourceAccessor on the service
#                       acme-observatory-deployer -> artifactregistry.writer on repo "irrops"
#                       acme-observatory-deployer -> iam.serviceAccountUser on acme-irrops-demo
#   GitHub variables    IRROPS_DEMO_SERVICE, IRROPS_AR_REPO (OBSERVATORY_WIF_PROVIDER and
#                       OBSERVATORY_DEPLOYER_SA are reused, they already exist)
# If the custom IAP OAuth client from 55-iap-oauth-client.sh is configured, it is applied to the
# new service too, so external domains (google.com) are admitted.
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"

P="${PROJECT_ID}"; R="${REGION}"; REPO="${GITHUB_REPO}"
PNUM="$(gcloud projects describe "${P}" --format='value(projectNumber)')"
SERVICE="acme-irrops-demo"
AR_REPO="irrops"
SA_DEMO="acme-irrops-demo@${P}.iam.gserviceaccount.com"
SA_DEPL="acme-observatory-deployer@${P}.iam.gserviceaccount.com"
PLACEHOLDER_SVC="us-docker.pkg.dev/cloudrun/container/hello"

log() { printf '\n==> %s\n' "$*"; }

log "APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com iap.googleapis.com \
  iam.googleapis.com --project="${P}"

log "Artifact Registry: ${AR_REPO} (${R})"
gcloud artifacts repositories describe "${AR_REPO}" --location="${R}" --project="${P}" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "${AR_REPO}" --repository-format=docker --location="${R}" --project="${P}" \
    --description="acme IRROPS demo images"

log "Service account: ${SA_DEMO} (no project roles)"
gcloud iam service-accounts describe "${SA_DEMO}" --project="${P}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create acme-irrops-demo --project="${P}" \
    --display-name="ACME IRROPS demo runtime (no GCP access)"

log "IAM: deployer (only what the demo deploy needs)"
gcloud artifacts repositories add-iam-policy-binding "${AR_REPO}" --location="${R}" --project="${P}" \
  --member="serviceAccount:${SA_DEPL}" --role=roles/artifactregistry.writer --format=none
gcloud iam service-accounts add-iam-policy-binding "${SA_DEMO}" --project="${P}" \
  --member="serviceAccount:${SA_DEPL}" --role=roles/iam.serviceAccountUser --format=none

log "Cloud Run: ${SERVICE} (placeholder, IAP, single instance)"
if ! gcloud run services describe "${SERVICE}" --region="${R}" --project="${P}" >/dev/null 2>&1; then
  gcloud beta run deploy "${SERVICE}" --region="${R}" --project="${P}" --image="${PLACEHOLDER_SVC}" \
    --service-account="${SA_DEMO}" --no-allow-unauthenticated --iap --max-instances=1 --quiet \
    --set-env-vars="LLM_MODE=fake"
fi
gcloud run services add-iam-policy-binding "${SERVICE}" --region="${R}" --project="${P}" \
  --member="serviceAccount:${SA_DEPL}" --role=roles/run.developer --format=none
gcloud run services add-iam-policy-binding "${SERVICE}" --region="${R}" --project="${P}" \
  --member="serviceAccount:service-${PNUM}@gcp-sa-iap.iam.gserviceaccount.com" --role=roles/run.invoker --format=none
read -ra DOMS <<<"${WEB_DOMAINS}"
for d in "${DOMS[@]}"; do
  gcloud iap web add-iam-policy-binding --project="${P}" --resource-type=cloud-run --service="${SERVICE}" \
    --region="${R}" --member="domain:${d}" --role=roles/iap.httpsResourceAccessor --condition=None --format=none
done

if gcloud secrets describe acme-iap-oauth-client-id --project="${P}" >/dev/null 2>&1 \
   && gcloud secrets describe acme-iap-oauth-client-secret --project="${P}" >/dev/null 2>&1; then
  log "IAP: applying the custom OAuth client (external domains)"
  bash "${INFRA_DIR}/55-iap-oauth-client.sh" --reapply
else
  log "IAP: no custom OAuth client configured yet; run infra/55-iap-oauth-client.sh for external domains"
fi

log "GitHub Actions variables (non-secret)"
gh variable set IRROPS_DEMO_SERVICE -R "${REPO}" --body "${SERVICE}"
gh variable set IRROPS_AR_REPO -R "${REPO}" --body "${R}-docker.pkg.dev/${P}/${AR_REPO}"

URL="$(gcloud run services describe "${SERVICE}" --region="${R}" --project="${P}" --format='value(status.url)')"
log "done: demo URL (IAP): ${URL}"
