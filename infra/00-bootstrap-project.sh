#!/usr/bin/env bash
# Task 2 — create and prepare the GCP project for the factory. Idempotent.
#
# - Creates the project under folder dubofolder (suffixes the ID if taken and
#   writes the final ID back into infra/deploy-config.json).
# - Links billing, enables APIs, prints effective org policies.
# - Ensures a `default` VPC + `default` subnet in the region (required by
#   Scion's single-node-vm deploy.sh; hardened orgs may skip default network).
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"

APIS=(compute run iap cloudbuild artifactregistry aiplatform iamcredentials logging monitoring orgpolicy)

# Never block on interactive prompts (e.g. "enable API and retry? (y/N)").
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

log() { printf '\n==> %s\n' "$*"; }

# --- 1. Project -------------------------------------------------------------
if gcloud projects describe "${PROJECT_ID}" >/dev/null 2>&1; then
  log "Project ${PROJECT_ID} already exists"
else
  log "Creating project ${PROJECT_ID} under folder ${FOLDER_ID}"
  if ! out="$(gcloud projects create "${PROJECT_ID}" --folder="${FOLDER_ID}" \
                --name="acme-airlines-factory" 2>&1)"; then
    echo "${out}"
    if grep -qiE 'already (in use|exists)|ALREADY_EXISTS' <<<"${out}"; then
      new_id="${PROJECT_ID}-$(openssl rand -hex 2)"
      log "ID taken; retrying as ${new_id}"
      gcloud projects create "${new_id}" --folder="${FOLDER_ID}" --name="acme-airlines-factory"
      tmp="$(mktemp)"; jq --arg id "${new_id}" '.project_id=$id' "${CONFIG_FILE}" >"${tmp}" && mv "${tmp}" "${CONFIG_FILE}"
      PROJECT_ID="${new_id}"
    else
      exit 1
    fi
  fi
fi
gcloud config set project "${PROJECT_ID}" >/dev/null

# --- 2. Billing -------------------------------------------------------------
if [[ "$(gcloud billing projects describe "${PROJECT_ID}" --format='value(billingEnabled)')" != "True" ]]; then
  log "Linking billing account ${BILLING_ACCOUNT}"
  gcloud billing projects link "${PROJECT_ID}" --billing-account="${BILLING_ACCOUNT}"
else
  log "Billing already enabled"
fi

# --- 3. APIs ----------------------------------------------------------------
log "Enabling APIs: ${APIS[*]}"
gcloud services enable "${APIS[@]/%/.googleapis.com}" --project="${PROJECT_ID}"

# --- 4. Org policies (informational) ---------------------------------------
log "Effective org policies (inherited from org/folder) for key constraints"
for c in compute.skipDefaultNetworkCreation compute.requireShieldedVm compute.vmExternalIpAccess \
         iam.allowedPolicyMemberDomains iam.disableServiceAccountKeyCreation compute.requireOsLogin \
         run.allowedIngress gcp.resourceLocations; do
  printf -- '--- %s\n' "${c}"
  gcloud org-policies describe "${c}" --project="${PROJECT_ID}" --effective \
    --format='yaml(spec.rules)' 2>&1 | sed 's/^/    /' || true
done

# --- 5. Default network -----------------------------------------------------
if gcloud compute networks describe default --project="${PROJECT_ID}" >/dev/null 2>&1; then
  log "Network default exists"
else
  log "Creating auto-mode network default"
  if ! gcloud compute networks create default --project="${PROJECT_ID}" --subnet-mode=auto; then
    log "Auto-mode rejected; creating custom-mode network + subnet"
    gcloud compute networks create default --project="${PROJECT_ID}" --subnet-mode=custom
  fi
fi
if ! gcloud compute networks subnets describe default --region="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  log "Creating subnet default in ${REGION}"
  gcloud compute networks subnets create default --project="${PROJECT_ID}" \
    --network=default --region="${REGION}" --range=10.128.0.0/20
fi

log "Bootstrap complete: project=${PROJECT_ID} number=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
