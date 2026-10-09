#!/usr/bin/env bash
# Shared variables and helpers for the acme-airlines factory infra scripts.
# Source this file; do not execute it.
#
# All values derive from infra/deploy-config.json (the same file passed to
# Scion's scripts/single-node-vm/deploy.sh), so the project ID is never
# hard-coded anywhere else.

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${INFRA_DIR}/.." && pwd)"
CONFIG_FILE="${INFRA_DIR}/deploy-config.json"

# Scion source checkout used to run deploy.sh (override with SCION_SRC).
SCION_SRC="${SCION_SRC:-/Users/duboc/local/projects/scion}"
SCION_VERSION="${SCION_VERSION:-nightly-20261009}"

# Dedicated gcloud configuration so the operator's default config is untouched.
export CLOUDSDK_ACTIVE_CONFIG_NAME="${CLOUDSDK_ACTIVE_CONFIG_NAME:-acme-factory}"

FOLDER_ID="543080121395"            # dubofolder
BILLING_ACCOUNT="01820C-6329B4-8A7409"

cfg() { jq -r "$1" "${CONFIG_FILE}"; }

PROJECT_ID="$(cfg '.project_id')"
REGION="$(cfg '.region')"
HUB_NAME="$(cfg '.hub_name')"
VM_NAME="scion-hub-${HUB_NAME}"
VM_SA="scion-hub-${HUB_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
PROXY_SVC="scion-hub-${HUB_NAME}-iap-proxy"

# Zone of the existing VM; falls back to <region>-b like deploy.sh does.
resolve_zone() {
  local z
  z="$(gcloud compute instances list --project="${PROJECT_ID}" \
        --filter="name=${VM_NAME}" --format='value(zone.basename())' 2>/dev/null | head -1)"
  echo "${z:-${REGION}-b}"
}

# Run a command on the hub VM over IAP SSH.
vm_ssh() {
  gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" \
    --tunnel-through-iap --quiet --command="$1"
}

export PROJECT_ID REGION HUB_NAME VM_NAME VM_SA PROXY_SVC
export REPO_ROOT FOLDER_ID BILLING_ACCOUNT SCION_SRC SCION_VERSION CONFIG_FILE
