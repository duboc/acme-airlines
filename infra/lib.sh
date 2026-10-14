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

# Factory parameters (no secrets). Values below are fallbacks if factory.env lacks them.
# shellcheck source=infra/factory.env
[[ -f "${INFRA_DIR}/factory.env" ]] && source "${INFRA_DIR}/factory.env"

# Clean Scion source checkout at the pinned release tag, used to run deploy.sh.
# 10-deploy-hub.sh clones it here if missing (override with SCION_SRC).
SCION_VERSION="${SCION_VERSION:-nightly-20261009}"
SCION_SRC="${SCION_SRC:-${REPO_ROOT}/.cache/scion-${SCION_VERSION}}"

# Dedicated gcloud configuration so the operator's default config is untouched.
export CLOUDSDK_ACTIVE_CONFIG_NAME="${CLOUDSDK_ACTIVE_CONFIG_NAME:-acme-factory}"
# Never block on interactive gcloud prompts (e.g. "enable API and retry? (y/N)").
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

FOLDER_ID="${FOLDER_ID:-543080121395}"
BILLING_ACCOUNT="${BILLING_ACCOUNT:-01820C-6329B4-8A7409}"
ORG_DOMAIN="${ORG_DOMAIN:-duboc.altostrat.com}"
WEB_DOMAINS="${WEB_DOMAINS:-google.com duboc.altostrat.com}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@duboc.altostrat.com}"
DEFAULT_USER_ROLE="${DEFAULT_USER_ROLE:-viewer}"
GITHUB_REPO="${GITHUB_REPO:-duboc/acme-airlines}"
HUB_PROJECT_SLUG="${HUB_PROJECT_SLUG:-duboc-acme-airlines}"
AGENT_TEAM_SHA="${AGENT_TEAM_SHA:-3650f1b03411116fcbf37852bfec6911db64b569}"
IMPECCABLE_SHA="${IMPECCABLE_SHA:-d631a8827f99414d2b6daba4ef08b7f8701751d7}"
BUDGET_USD="${BUDGET_USD:-2000}"
TICK_CRON="${TICK_CRON:-0 */6 * * *}"
DEPLOY_ZONE="${DEPLOY_ZONE:-us-central1-a}"
GCLOUD_CLIENT_ID="${GCLOUD_CLIENT_ID:-32555940559.apps.googleusercontent.com}"

cfg() { jq -r "$1" "${CONFIG_FILE}"; }

PROJECT_ID="$(cfg '.project_id')"
REGION="$(cfg '.region')"
HUB_NAME="$(cfg '.hub_name')"
FACTORY_PREFIX="${FACTORY_PREFIX:-${HUB_NAME}}"
BUDGET_NAME="${BUDGET_NAME:-${PROJECT_ID}-monthly}"
VM_NAME="scion-hub-${HUB_NAME}"
VM_SA="scion-hub-${HUB_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
PROXY_SVC="scion-hub-${HUB_NAME}-iap-proxy"

: "${PROJECT_ID:?PROJECT_ID missing in deploy-config.json}"
: "${REGION:?REGION missing in deploy-config.json}"
: "${HUB_NAME:?HUB_NAME missing in deploy-config.json}"
: "${FOLDER_ID:?FOLDER_ID missing in factory.env}"
: "${BILLING_ACCOUNT:?BILLING_ACCOUNT missing in factory.env}"
: "${ORG_DOMAIN:?ORG_DOMAIN missing in factory.env}"
: "${ADMIN_EMAIL:?ADMIN_EMAIL missing in factory.env}"
: "${GITHUB_REPO:?GITHUB_REPO missing in factory.env}"
: "${HUB_PROJECT_SLUG:?HUB_PROJECT_SLUG missing in factory.env}"

# Retry a command with linear backoff: retry <attempts> <delay_seconds> <cmd...>
retry() {
  local attempts="$1" delay="$2"; shift 2
  local i=1
  while true; do
    "$@" && return 0
    (( i >= attempts )) && return 1
    sleep "$(( delay * i ))"
    (( i++ ))
  done
}

# Models probed by `verify.sh vertex` on the Vertex AI global endpoint.
GEMINI_TEST_MODELS="${GEMINI_TEST_MODELS:-gemini-4-argon gemini-3.8-flash}"
CLAUDE_TEST_MODELS="${CLAUDE_TEST_MODELS:-claude-opus-5-5 claude-sonnet-5-5 claude-haiku-5-5}"
export GEMINI_TEST_MODELS CLAUDE_TEST_MODELS

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

# Run `scion <args...>` on the VM against http://localhost:8080 as the operator
# (requires infra/30-hub-admin-access.sh enable). The Google ID token travels
# over SSH stdin, never in argv or files. Extra stdin for the scion command can
# be supplied with HUB_CLI_STDIN (e.g. a secret value), sent after the token.
hub_cli() {
  local tok args
  tok="$(gcloud auth print-identity-token 2>/dev/null)"
  printf -v args '%q ' "$@"
  { printf '%s\n' "${tok}"; if [[ -n "${HUB_CLI_STDIN:-}" ]]; then printf '%s' "${HUB_CLI_STDIN}"; fi; } | \
    gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" \
      --tunnel-through-iap --quiet --command="
        IFS= read -r SCION_HUB_TOKEN; export SCION_HUB_TOKEN
        export SCION_HUB_ENDPOINT=http://localhost:8080
        mkdir -p ~/acme-cli && cd ~/acme-cli && scion ${args}"
}

export PROJECT_ID REGION HUB_NAME FACTORY_PREFIX BUDGET_NAME VM_NAME VM_SA PROXY_SVC
export REPO_ROOT FOLDER_ID BILLING_ACCOUNT SCION_SRC SCION_VERSION CONFIG_FILE
export ORG_DOMAIN WEB_DOMAINS ADMIN_EMAIL DEFAULT_USER_ROLE GITHUB_REPO HUB_PROJECT_SLUG AGENT_TEAM_SHA IMPECCABLE_SHA BUDGET_USD TICK_CRON DEPLOY_ZONE GCLOUD_CLIENT_ID
