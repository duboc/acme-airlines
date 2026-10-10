#!/usr/bin/env bash
# Budget guardrail for the factory. Idempotent.
#
#   - Monthly budget of BUDGET_USD (default 1000) on project acme-airlines-factory,
#     email alerts to billing admins at 50% / 90% / 100% of ACTUAL spend.
#   - Notifications are published to Pub/Sub topic acme-budget-alerts and pushed
#     (OIDC) to Cloud Run service acme-budget-guard. At >= 100% actual spend the
#     guard SUSPENDS ALL AGENTS of the acme-airlines hub project (token spend stops;
#     the VM, hub and UI keep running, by product-owner rule). Resume: infra/60-factory.sh resume
#   - The guard talks to the hub over the VPC (Direct VPC egress -> VM:8080) with a
#     project-scoped hub user access token (agent:list, agent:lifecycle) kept in
#     Secret Manager. It has NO Compute permissions.
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

BUDGET_USD="${BUDGET_USD:-1000}"
BUDGET_NAME="acme-airlines-factory-monthly"
TOPIC="acme-budget-alerts"
TOKEN_SECRET="acme-guard-hub-token"
SUB="acme-budget-guard-push"
SVC="acme-budget-guard"
SA="${SVC}@${PROJECT_ID}.iam.gserviceaccount.com"
ZONE="$(resolve_zone)"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

echo "==> APIs"
gcloud services enable billingbudgets.googleapis.com pubsub.googleapis.com run.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com --project="${PROJECT_ID}"

echo "==> Service account ${SA} (no Compute permissions; never stops the VM)"
gcloud iam service-accounts describe "${SA}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SVC}" --project="${PROJECT_ID}" --display-name="ACME budget guard"
# Remove the VM-stop permission granted by earlier versions of this script.
gcloud compute instances remove-iam-policy-binding "${VM_NAME}" --zone="${ZONE}" --project="${PROJECT_ID}" \
  --member="serviceAccount:${SA}" --role=roles/compute.instanceAdmin.v1 --format=none 2>/dev/null || true

echo "==> Hub access token for the guard (Secret Manager: ${TOKEN_SECRET})"
gcloud services enable secretmanager.googleapis.com --project="${PROJECT_ID}"
gcloud secrets describe "${TOKEN_SECRET}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud secrets create "${TOKEN_SECRET}" --project="${PROJECT_ID}" --replication-policy=automatic
if [[ -z "$(gcloud secrets versions list "${TOKEN_SECRET}" --project="${PROJECT_ID}" --filter=state=ENABLED --format='value(name)' | head -1)" || "${ROTATE_TOKEN:-0}" == 1 ]]; then
  bash "${INFRA_DIR}/30-hub-admin-access.sh" enable >/dev/null
  trap 'bash "${INFRA_DIR}/30-hub-admin-access.sh" disable >/dev/null || true' EXIT
  # Mint a project-scoped UAT and pipe it straight into Secret Manager (never printed or stored on disk).
  hub_cli hub token create --project duboc-acme-airlines --name acme-budget-guard \
      --scopes agent:list,agent:lifecycle --expires 90d --purpose "budget guard: suspend agents at 100% budget" --json 2>/dev/null \
    | jq -er '.token' | tr -d '\n' | gcloud secrets versions add "${TOKEN_SECRET}" --project="${PROJECT_ID}" --data-file=- --format=none
  bash "${INFRA_DIR}/30-hub-admin-access.sh" disable >/dev/null && trap - EXIT
fi
gcloud secrets add-iam-policy-binding "${TOKEN_SECRET}" --project="${PROJECT_ID}" \
  --member="serviceAccount:${SA}" --role=roles/secretmanager.secretAccessor --format=none
VM_IP="$(gcloud compute instances describe "${VM_NAME}" --zone="${ZONE}" --project="${PROJECT_ID}" \
  --format='value(networkInterfaces[0].networkIP)')"

echo "==> Build service account (the org disables automatic grants to the default compute SA)"
BUILD_SA="acme-builder@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts describe "${BUILD_SA}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create acme-builder --project="${PROJECT_ID}" --display-name="ACME Cloud Build"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="serviceAccount:${BUILD_SA}" \
  --role=roles/cloudbuild.builds.builder --condition=None --format=none
# The deployer must be able to act as the build SA.
gcloud iam service-accounts add-iam-policy-binding "${BUILD_SA}" --project="${PROJECT_ID}" \
  --member="user:$(gcloud config get-value account 2>/dev/null)" --role=roles/iam.serviceAccountUser --format=none

echo "==> Cloud Run service ${SVC}"
gcloud run deploy "${SVC}" --project="${PROJECT_ID}" --region="${REGION}" \
  --source="${INFRA_DIR}/budget-guard" --service-account="${SA}" \
  --build-service-account="projects/${PROJECT_ID}/serviceAccounts/${BUILD_SA}" \
  --no-allow-unauthenticated --max-instances=1 --memory=512Mi \
  --network=default --subnet=default --vpc-egress=private-ranges-only \
  --set-env-vars="HUB_ENDPOINT=http://${VM_IP}:8080,HUB_PROJECT=duboc-acme-airlines" \
  --set-secrets="SCION_HUB_TOKEN=${TOKEN_SECRET}:latest" --quiet
URL="$(gcloud run services describe "${SVC}" --region="${REGION}" --project="${PROJECT_ID}" --format='value(status.url)')"
gcloud run services add-iam-policy-binding "${SVC}" --region="${REGION}" --project="${PROJECT_ID}" \
  --member="serviceAccount:${SA}" --role=roles/run.invoker --format=none

echo "==> Pub/Sub ${TOPIC} -> ${URL}"
gcloud pubsub topics describe "${TOPIC}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud pubsub topics create "${TOPIC}" --project="${PROJECT_ID}"
# Pub/Sub must be able to mint OIDC tokens as the guard SA for authenticated push.
gcloud iam service-accounts add-iam-policy-binding "${SA}" --project="${PROJECT_ID}" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountTokenCreator --format=none
if gcloud pubsub subscriptions describe "${SUB}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud pubsub subscriptions update "${SUB}" --project="${PROJECT_ID}" \
    --push-endpoint="${URL}/" --push-auth-service-account="${SA}" --push-auth-token-audience="${URL}"
else
  gcloud pubsub subscriptions create "${SUB}" --project="${PROJECT_ID}" --topic="${TOPIC}" \
    --push-endpoint="${URL}/" --push-auth-service-account="${SA}" --push-auth-token-audience="${URL}" --ack-deadline=60
fi

echo "==> Budget ${BUDGET_NAME}: ${BUDGET_USD} USD/month, alerts 50/90/100%"
existing="$(gcloud billing budgets list --billing-account="${BILLING_ACCOUNT}" \
  --filter="displayName=${BUDGET_NAME}" --format='value(name)' | head -1)"
common=(--billing-account="${BILLING_ACCOUNT}" --display-name="${BUDGET_NAME}"
        --budget-amount="${BUDGET_USD}USD"
        --notifications-rule-pubsub-topic="projects/${PROJECT_ID}/topics/${TOPIC}"
        --filter-projects="projects/${PROJECT_ID}")
if [[ -z "${existing}" ]]; then
  gcloud billing budgets create "${common[@]}" \
    --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
else
  gcloud billing budgets update "${existing}" "${common[@]}" --clear-threshold-rules \
    --add-threshold-rule=percent=0.5 --add-threshold-rule=percent=0.9 --add-threshold-rule=percent=1.0
fi
echo "done"
