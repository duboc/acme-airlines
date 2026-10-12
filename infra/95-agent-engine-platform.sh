#!/usr/bin/env bash
# Addendum D: platform for running the IRROPS ADK agents on Vertex AI Agent Engine. Idempotent.
#
#   Staging bucket   gs://<project>-agent-engine (uniform access, us-central1)
#   Runtime SA       acme-agent-engine: aiplatform.user (Gemini, global), cloudtrace.agent,
#                    logging.logWriter, monitoring.metricWriter. Nothing else.
#   Deployer (WIF)   acme-observatory-deployer: aiplatform.user (create/update reasoning engines),
#                    storage.objectAdmin + storage.legacyBucketReader (buckets.get, #186) on the
#                    staging bucket, actAs acme-agent-engine
#   Service agent    service-<num>@gcp-sa-aiplatform-re: objectViewer on the bucket and
#                    TokenCreator on acme-agent-engine (needed to run engines as a custom SA)
#   Demo SA          acme-irrops-demo: aiplatform.user (query the engines via stream_query)
#   GitHub vars      AGENT_ENGINE_SA, AGENT_ENGINE_STAGING_BUCKET, AGENT_ENGINE_LOCATION
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"

P="${PROJECT_ID}"; R="${REGION}"; REPO="${GITHUB_REPO}"
PNUM="$(gcloud projects describe "${P}" --format='value(projectNumber)')"
BUCKET="gs://${P}-agent-engine"
SA_RT="acme-agent-engine@${P}.iam.gserviceaccount.com"
SA_DEPL="acme-observatory-deployer@${P}.iam.gserviceaccount.com"
SA_DEMO="acme-irrops-demo@${P}.iam.gserviceaccount.com"
RE_AGENT="service-${PNUM}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

log() { printf '\n==> %s\n' "$*"; }
# IAM is eventually consistent: a just-created SA can be "not found" for ~a minute.
retry() { local n; for n in 1 2 3 4 5 6; do "$@" && return 0; echo "  retry ${n}/6 in $((n*10))s: $1 $2 …" >&2; sleep $((n*10)); done; return 1; }
proj_role() { retry gcloud projects add-iam-policy-binding "${P}" --member="serviceAccount:$1" --role="$2" --condition=None --format=none; }

log "APIs"
gcloud services enable aiplatform.googleapis.com storage.googleapis.com cloudtrace.googleapis.com \
  logging.googleapis.com monitoring.googleapis.com --project="${P}"

log "Staging bucket ${BUCKET}"
gcloud storage buckets describe "${BUCKET}" >/dev/null 2>&1 || \
  gcloud storage buckets create "${BUCKET}" --project="${P}" --location="${R}" --uniform-bucket-level-access

log "Runtime SA ${SA_RT}"
gcloud iam service-accounts describe "${SA_RT}" --project="${P}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create acme-agent-engine --project="${P}" --display-name="ACME IRROPS agents on Agent Engine"
for role in roles/aiplatform.user roles/cloudtrace.agent roles/logging.logWriter roles/monitoring.metricWriter; do
  proj_role "${SA_RT}" "${role}"
done

log "Deployer (WIF) permissions"
proj_role "${SA_DEPL}" roles/aiplatform.user
retry gcloud storage buckets add-iam-policy-binding "${BUCKET}" --member="serviceAccount:${SA_DEPL}" \
  --role=roles/storage.objectAdmin --format=none
# The Vertex AI SDK reads bucket metadata (storage.buckets.get) before uploading engine
# packages; objectAdmin has no bucket-level permissions (#186). Read-only, this bucket only.
retry gcloud storage buckets add-iam-policy-binding "${BUCKET}" --member="serviceAccount:${SA_DEPL}" \
  --role=roles/storage.legacyBucketReader --format=none
retry gcloud iam service-accounts add-iam-policy-binding "${SA_RT}" --project="${P}" \
  --member="serviceAccount:${SA_DEPL}" --role=roles/iam.serviceAccountUser --format=none

log "Reasoning Engine service agent (${RE_AGENT})"
# The service agent may not exist until the first engine is created; create it explicitly.
gcloud beta services identity create --service=aiplatform.googleapis.com --project="${P}" --format=none 2>/dev/null || true
if gcloud storage buckets add-iam-policy-binding "${BUCKET}" --member="serviceAccount:${RE_AGENT}" \
     --role=roles/storage.objectViewer --format=none 2>/dev/null \
   && gcloud iam service-accounts add-iam-policy-binding "${SA_RT}" --project="${P}" \
     --member="serviceAccount:${RE_AGENT}" --role=roles/iam.serviceAccountTokenCreator --format=none 2>/dev/null; then
  echo "service agent bindings in place"
else
  echo "WARN: ${RE_AGENT} does not exist yet; re-run this script after the first engine deploy" >&2
fi

log "Demo SA may query engines"
proj_role "${SA_DEMO}" roles/aiplatform.user

log "GitHub Actions variables (non-secret)"
gh variable set AGENT_ENGINE_SA -R "${REPO}" --body "${SA_RT}"
gh variable set AGENT_ENGINE_STAGING_BUCKET -R "${REPO}" --body "${BUCKET}"
gh variable set AGENT_ENGINE_LOCATION -R "${REPO}" --body "${R}"
log "done"
