#!/usr/bin/env bash
# Factory watchdog (audit 2026-10-12): wake-on-change, self-healing, daily lead
# reincarnation, and PO alerting. Idempotent.
#
#   Service     Cloud Run acme-factory-watchdog (Direct VPC egress -> hub:8080)
#   UAT         Secret Manager acme-watchdog-hub-token (agent:list,read,message,lifecycle)
#   Schedules   acme-factory-watchdog (0 * * * * UTC   -> POST /check)
#               acme-lead-reincarnate (0 4 * * * UTC   -> POST /reincarnate)
#   PO alert    Log-based alert policy acme-factory-watchdog-po-alert -> email ADMIN_EMAIL
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

SVC="acme-factory-watchdog"
SA="${SVC}@${PROJECT_ID}.iam.gserviceaccount.com"
TOKEN_SECRET="acme-watchdog-hub-token"
GH_SECRET="acme-observatory-github-token"
STATE_BUCKET="${PROJECT_ID}-agent-engine"
CHECK_JOB="acme-factory-watchdog"
REINC_JOB="acme-lead-reincarnate"
ALERT_DISPLAY="acme-factory-watchdog-po-alert"
ZONE="$(resolve_zone)"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

echo "==> APIs"
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com monitoring.googleapis.com logging.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com --project="${PROJECT_ID}"

echo "==> Service account ${SA}"
gcloud iam service-accounts describe "${SA}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SVC}" --project="${PROJECT_ID}" --display-name="ACME factory watchdog"

echo "==> Hub UAT (${TOKEN_SECRET}) and PO notification subscription"
gcloud secrets describe "${TOKEN_SECRET}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud secrets create "${TOKEN_SECRET}" --project="${PROJECT_ID}" --replication-policy=automatic
if [[ -z "$(gcloud secrets versions list "${TOKEN_SECRET}" --project="${PROJECT_ID}" --filter=state=ENABLED --format='value(name)' | head -1)" || "${ROTATE_TOKEN:-0}" == 1 ]]; then
  bash "${INFRA_DIR}/30-hub-admin-access.sh" enable >/dev/null
  trap 'bash "${INFRA_DIR}/30-hub-admin-access.sh" disable >/dev/null || true' EXIT
  hub_cli hub token create --project "${HUB_PROJECT_SLUG}" --name "${SVC}" \
      --scopes agent:list,agent:read,agent:message,agent:lifecycle --expires 90d \
      --purpose "watchdog: wake on change, self-heal, daily reincarnate" --json 2>/dev/null \
    | jq -er '.token' | tr -d '\n' \
    | gcloud secrets versions add "${TOKEN_SECRET}" --project="${PROJECT_ID}" --data-file=- --format=none
  hub_cli notifications subscribe --project "${HUB_PROJECT_SLUG}" --agent lead \
    --triggers COMPLETED,WAITING_FOR_INPUT,LIMITS_EXCEEDED >/dev/null 2>&1 || true
  bash "${INFRA_DIR}/30-hub-admin-access.sh" disable || echo "WARN: disable reported an error; checking status" >&2
  [[ "$(bash "${INFRA_DIR}/30-hub-admin-access.sh" status 2>/dev/null | tail -1)" == "disabled" ]] \
    || { echo "ERROR: admin access still enabled; run: infra/30-hub-admin-access.sh disable" >&2; exit 1; }
  trap - EXIT
fi

for sec in "${TOKEN_SECRET}" "${GH_SECRET}"; do
  gcloud secrets add-iam-policy-binding "${sec}" --project="${PROJECT_ID}" \
    --member="serviceAccount:${SA}" --role=roles/secretmanager.secretAccessor --format=none
done

if gcloud storage buckets describe "gs://${STATE_BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud storage buckets add-iam-policy-binding "gs://${STATE_BUCKET}" --project="${PROJECT_ID}" \
    --member="serviceAccount:${SA}" --role=roles/storage.objectUser --quiet >/dev/null
fi

VM_IP="$(gcloud compute instances describe "${VM_NAME}" --zone="${ZONE}" --project="${PROJECT_ID}" \
  --format='value(networkInterfaces[0].networkIP)')"
BUILD_SA="acme-builder@${PROJECT_ID}.iam.gserviceaccount.com"

echo "==> Cloud Run service ${SVC}"
gcloud run deploy "${SVC}" --project="${PROJECT_ID}" --region="${REGION}" \
  --source="${INFRA_DIR}/watchdog" --service-account="${SA}" \
  --build-service-account="projects/${PROJECT_ID}/serviceAccounts/${BUILD_SA}" \
  --no-allow-unauthenticated --max-instances=1 --memory=512Mi --timeout=300 \
  --network=default --subnet=default --vpc-egress=private-ranges-only \
  --set-env-vars="HUB_ENDPOINT=http://${VM_IP}:8080,HUB_PROJECT=${HUB_PROJECT_SLUG},GITHUB_REPO=${GITHUB_REPO},STATE_BUCKET=${STATE_BUCKET}" \
  --set-secrets="SCION_HUB_TOKEN=${TOKEN_SECRET}:latest,GITHUB_TOKEN=${GH_SECRET}:latest" --quiet
URL="$(gcloud run services describe "${SVC}" --region="${REGION}" --project="${PROJECT_ID}" --format='value(status.url)')"
gcloud run services add-iam-policy-binding "${SVC}" --region="${REGION}" --project="${PROJECT_ID}" \
  --member="serviceAccount:${SA}" --role=roles/run.invoker --format=none

echo "==> Cloud Scheduler jobs (${CHECK_JOB}, ${REINC_JOB})"
# Trigger Cloud Scheduler service agent creation if needed, then grant OIDC token minting on ${SA}.
gcloud beta services identity create --service=cloudscheduler.googleapis.com --project="${PROJECT_ID}" >/dev/null 2>&1 || true
gcloud iam service-accounts add-iam-policy-binding "${SA}" --project="${PROJECT_ID}" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountTokenCreator --format=none

upsert_sched() {
  local name="$1" cron="$2" path="$3" body="$4"
  local common=(--location="${REGION}" --project="${PROJECT_ID}" --schedule="${cron}" --time-zone="Etc/UTC"
                --uri="${URL}${path}" --http-method=POST
                --message-body="${body}" --oidc-service-account-email="${SA}" --oidc-token-audience="${URL}"
                --attempt-deadline=180s)
  if gcloud scheduler jobs describe "${name}" --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud scheduler jobs update http "${name}" "${common[@]}" --update-headers="Content-Type=application/json" --quiet
  else
    gcloud scheduler jobs create http "${name}" "${common[@]}" --headers="Content-Type=application/json" --quiet
  fi
}
upsert_sched "${CHECK_JOB}" "${WATCHDOG_CRON:-0 * * * *}" "/check"       '{"mode":"check"}'
upsert_sched "${REINC_JOB}" "0 4 * * *"                   "/reincarnate" '{"mode":"reincarnate"}'

echo "==> PO email notification channel + log-based alert policy (${ALERT_DISPLAY})"
CHAN="$(gcloud alpha monitoring channels list --project="${PROJECT_ID}" \
  --filter="type=\"email\" AND labels.email_address=\"${ADMIN_EMAIL}\"" --format='value(name)' | head -1)"
if [[ -z "${CHAN}" ]]; then
  CHAN="$(gcloud alpha monitoring channels create --project="${PROJECT_ID}" \
    --display-name="Factory PO (${ADMIN_EMAIL})" --type=email \
    --channel-labels="email_address=${ADMIN_EMAIL}" --format='value(name)')"
fi
EXISTING_POL="$(gcloud alpha monitoring policies list --project="${PROJECT_ID}" \
  --filter="displayName=\"${ALERT_DISPLAY}\"" --format='value(name)' | head -1)"
if [[ -z "${EXISTING_POL}" ]]; then
  POL_JSON="$(jq -n --arg dn "${ALERT_DISPLAY}" --arg ch "${CHAN}" '{
    displayName: $dn,
    combiner: "OR",
    enabled: true,
    notificationChannels: [$ch],
    alertStrategy: {
      notificationRateLimit: {period: "1800s"},
      autoClose: "3600s"
    },
    conditions: [{
      displayName: "Watchdog PO alert (stalled/missing lead or watchdog error)",
      conditionMatchedLog: {
        filter: "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"acme-factory-watchdog\" AND jsonPayload.alert_po=true"
      }
    }]
  }')"
  gcloud alpha monitoring policies create --project="${PROJECT_ID}" --policy="${POL_JSON}" >/dev/null
fi
echo "done: ${SVC} deployed, schedules active, PO alert policy wired to ${ADMIN_EMAIL}"
