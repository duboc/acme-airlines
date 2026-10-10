#!/usr/bin/env bash
# Budget guardrail for the factory. Idempotent.
#
#   - Monthly budget of BUDGET_USD (default 1000) on project acme-airlines-factory,
#     email alerts to billing admins at 50% / 90% / 100% of ACTUAL spend.
#   - Notifications are published to Pub/Sub topic acme-budget-alerts and pushed
#     (OIDC) to Cloud Run service acme-budget-guard. At >= 100% actual spend the
#     guard STOPS THE HUB VM (all state kept on disk). Resume: infra/60-factory.sh start-vm
#   - The guard's service account may only manage this one VM (instance-level IAM).
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

BUDGET_USD="${BUDGET_USD:-1000}"
BUDGET_NAME="acme-airlines-factory-monthly"
TOPIC="acme-budget-alerts"
SUB="acme-budget-guard-push"
SVC="acme-budget-guard"
SA="${SVC}@${PROJECT_ID}.iam.gserviceaccount.com"
ZONE="$(resolve_zone)"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

echo "==> APIs"
gcloud services enable billingbudgets.googleapis.com pubsub.googleapis.com run.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com --project="${PROJECT_ID}"

echo "==> Service account ${SA} (may only stop/start ${VM_NAME})"
gcloud iam service-accounts describe "${SA}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SVC}" --project="${PROJECT_ID}" --display-name="ACME budget guard"
gcloud compute instances add-iam-policy-binding "${VM_NAME}" --zone="${ZONE}" --project="${PROJECT_ID}" \
  --member="serviceAccount:${SA}" --role=roles/compute.instanceAdmin.v1 --format=none

echo "==> Cloud Run service ${SVC}"
gcloud run deploy "${SVC}" --project="${PROJECT_ID}" --region="${REGION}" \
  --source="${INFRA_DIR}/budget-guard" --service-account="${SA}" \
  --no-allow-unauthenticated --max-instances=1 --memory=256Mi \
  --set-env-vars="PROJECT_ID=${PROJECT_ID},ZONE=${ZONE},INSTANCE=${VM_NAME}" --quiet
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
    --push-endpoint="${URL}/" --push-auth-service-account="${SA}"
else
  gcloud pubsub subscriptions create "${SUB}" --project="${PROJECT_ID}" --topic="${TOPIC}" \
    --push-endpoint="${URL}/" --push-auth-service-account="${SA}" --ack-deadline=60
fi

echo "==> Budget ${BUDGET_NAME}: ${BUDGET_USD} USD/month, alerts 50/90/100%"
existing="$(gcloud billing budgets list --billing-account="${BILLING_ACCOUNT}" \
  --filter="displayName=${BUDGET_NAME}" --format='value(name)' | head -1)"
args=(--billing-account="${BILLING_ACCOUNT}" --display-name="${BUDGET_NAME}"
      --budget-amount="${BUDGET_USD}USD"
      --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
      --notifications-rule-pubsub-topic="projects/${PROJECT_ID}/topics/${TOPIC}")
if [[ -z "${existing}" ]]; then
  gcloud billing budgets create "${args[@]}" --filter-projects="projects/${PROJECT_ID}"
else
  gcloud billing budgets update "${existing}" "${args[@]}" --filter-projects="projects/${PROJECT_ID}"
fi
echo "done"
