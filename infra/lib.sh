#!/usr/bin/env bash
# Shared variables and helpers for the acme-airlines factory infra scripts.
# Source this file; do not execute it.
#
# Public defaults live in infra/factory.env (tracked).
# Sensitive org/billing/domain values live in infra/factory.local.env (gitignored,
# backed up in GCP Secret Manager as `<hub_name>-factory-local-env`).

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${INFRA_DIR}/.." && pwd)"
CONFIG_FILE="${INFRA_DIR}/deploy-config.json"
LOCAL_ENV_FILE="${INFRA_DIR}/factory.local.env"

# Public factory parameters (no org/billing/domain identifiers).
# shellcheck source=infra/factory.env
[[ -f "${INFRA_DIR}/factory.env" ]] && source "${INFRA_DIR}/factory.env"

cfg() { jq -r "$1" "${CONFIG_FILE}"; }

PROJECT_ID="$(cfg '.project_id')"
REGION="$(cfg '.region')"
HUB_NAME="$(cfg '.hub_name')"
HUB_PROJECT="${HUB_PROJECT:-${PROJECT_ID}}"
SANDBOX_PROJECT="${SANDBOX_PROJECT:-${PROJECT_ID}}"
SANDBOX_REGION="${SANDBOX_REGION:-${REGION}}"
FACTORY_PREFIX="${FACTORY_PREFIX:-${HUB_NAME}}"
BUDGET_NAME="${BUDGET_NAME:-${PROJECT_ID}-monthly}"
VM_NAME="scion-hub-${HUB_NAME}"
VM_SA="scion-hub-${HUB_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
PROXY_SVC="scion-hub-${HUB_NAME}-iap-proxy"

# Dedicated gcloud configuration so the operator's default config is untouched.
export CLOUDSDK_ACTIVE_CONFIG_NAME="${CLOUDSDK_ACTIVE_CONFIG_NAME:-acme-factory}"
# Never block on interactive gcloud prompts (e.g. "enable API and retry? (y/N)").
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

# Load gitignored local overrides; if missing on a fresh workstation, hydrate from Secret Manager.
if [[ -f "${LOCAL_ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${LOCAL_ENV_FILE}"
elif [[ -z "${VERIFY_LIB_ONLY:-}" && -z "${ADMIN_EMAIL:-}" ]] && command -v gcloud >/dev/null 2>&1; then
  if secret_payload="$(gcloud secrets versions access latest \
       --secret="${FACTORY_PREFIX}-factory-local-env" \
       --project="${PROJECT_ID}" 2>/dev/null)" && [[ -n "${secret_payload}" ]]; then
    umask 077
    printf '%s\n' "${secret_payload}" > "${LOCAL_ENV_FILE}"
    # shellcheck disable=SC1090
    source "${LOCAL_ENV_FILE}"
  fi
fi

# Clean Scion source checkout at the pinned release tag, used to run deploy.sh.
SCION_VERSION="${SCION_VERSION:-nightly-20261009}"
SCION_SRC="${SCION_SRC:-${REPO_ROOT}/.cache/scion-${SCION_VERSION}}"

DEFAULT_USER_ROLE="${DEFAULT_USER_ROLE:-viewer}"
GITHUB_REPO="${GITHUB_REPO:-duboc/acme-airlines}"
BUILD_PROJECT="${BUILD_PROJECT:-duboc-acme-airlines}"
EVAL_PROJECT="${EVAL_PROJECT:-acme-airlines-eval}"
HUB_PROJECT_SLUG="${HUB_PROJECT_SLUG:-${BUILD_PROJECT}}"
BUILD_BRANCH="${BUILD_BRANCH:-main}"
EVAL_BRANCH="${EVAL_BRANCH:-eval-dashboard}"
SERVING_SERVICE="${SERVING_SERVICE:-acme-irrops-demo}"
DASHBOARD_SERVICE="${DASHBOARD_SERVICE:-acme-observatory}"
DEPLOYER_SA_NAME="${DEPLOYER_SA_NAME:-acme-observatory-deployer}"
RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-acme-irrops-demo}"
DEPLOYER_SA="${DEPLOYER_SA:-${DEPLOYER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"
RUNTIME_SA="${RUNTIME_SA:-${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"
AGENT_SA="${AGENT_SA:-${FACTORY_PREFIX}-agent@${PROJECT_ID}.iam.gserviceaccount.com}"
PO_EMAIL="${PO_EMAIL:-${ADMIN_EMAIL:-}}"
AGENT_TEAM_SHA="${AGENT_TEAM_SHA:-3650f1b03411116fcbf37852bfec6911db64b569}"
IMPECCABLE_SHA="${IMPECCABLE_SHA:-d631a8827f99414d2b6daba4ef08b7f8701751d7}"
BUDGET_USD="${BUDGET_USD:-2000}"
HEARTBEAT_CRON="${HEARTBEAT_CRON:-*/15 * * * *}"
TICK_CRON="${TICK_CRON:-${HEARTBEAT_CRON}}"
WATCHDOG_CRON="${WATCHDOG_CRON:-0 * * * *}"
DEPLOY_ZONE="${DEPLOY_ZONE:-us-central1-a}"
GCLOUD_CLIENT_ID="${GCLOUD_CLIENT_ID:-32555940559.apps.googleusercontent.com}"

: "${PROJECT_ID:?PROJECT_ID missing in deploy-config.json}"
: "${REGION:?REGION missing in deploy-config.json}"
: "${HUB_NAME:?HUB_NAME missing in deploy-config.json}"
: "${GITHUB_REPO:?GITHUB_REPO missing in factory.env}"
: "${BUILD_PROJECT:?BUILD_PROJECT missing in factory.env}"
: "${EVAL_PROJECT:?EVAL_PROJECT missing in factory.env}"

if [[ -z "${VERIFY_LIB_ONLY:-}" ]]; then
  : "${FOLDER_ID:?FOLDER_ID missing — set in infra/factory.local.env (or Secret Manager ${FACTORY_PREFIX}-factory-local-env)}"
  : "${BILLING_ACCOUNT:?BILLING_ACCOUNT missing — set in infra/factory.local.env (or Secret Manager ${FACTORY_PREFIX}-factory-local-env)}"
  : "${ORG_DOMAIN:?ORG_DOMAIN missing — set in infra/factory.local.env (or Secret Manager ${FACTORY_PREFIX}-factory-local-env)}"
  : "${WEB_DOMAINS:?WEB_DOMAINS missing — set in infra/factory.local.env (or Secret Manager ${FACTORY_PREFIX}-factory-local-env)}"
  : "${ADMIN_EMAIL:?ADMIN_EMAIL missing — set in infra/factory.local.env (or Secret Manager ${FACTORY_PREFIX}-factory-local-env)}"
fi

# Retry a command with linear backoff: retry <attempts> <delay_seconds> <cmd...>
retry() {
  local attempts="$1" delay="$2"; shift 2
  local i=1 rc=0
  while true; do
    rc=0
    "$@" && return 0 || rc=$?
    (( i >= attempts )) && return "${rc}"
    sleep "$(( delay * i ))"
    (( i++ ))
  done
}

# Models probed by `verify.sh vertex` on the Vertex AI global endpoint.
GEMINI_TEST_MODELS="${GEMINI_TEST_MODELS:-gemini-4-argon gemini-3.8-flash}"
CLAUDE_TEST_MODELS="${CLAUDE_TEST_MODELS:-claude-opus-5-5 claude-sonnet-5-5 claude-haiku-5-5}"
export GEMINI_TEST_MODELS CLAUDE_TEST_MODELS

# Zone of the existing VM; falls back to <region>-b like deploy.sh does.
_CACHED_VM_ZONE=""
resolve_zone() {
  if [[ -n "${_CACHED_VM_ZONE}" ]]; then
    echo "${_CACHED_VM_ZONE}"
    return 0
  fi
  local z
  z="$(gcloud compute instances list --project="${PROJECT_ID}" \
        --filter="name=${VM_NAME}" --format='value(zone.basename())' 2>/dev/null | head -1)"
  _CACHED_VM_ZONE="${z:-${REGION}-b}"
  echo "${_CACHED_VM_ZONE}"
}

# Run a command on the hub VM over IAP SSH (retries transient SSH transport exit 255).
vm_ssh() {
  local z i=1 rc=0
  z="$(resolve_zone)"
  while true; do
    rc=0
    gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="${z}" \
      --tunnel-through-iap --quiet --command="$1" && return 0 || rc=$?
    [[ "${rc}" -eq 255 && "${i}" -lt 3 ]] || return "${rc}"
    sleep "$(( 2 * i ))"
    (( i++ ))
  done
}

# Run `scion <args...>` on the VM against http://localhost:8080 as the operator
# (requires infra/30-hub-admin-access.sh enable). The Google ID token travels
# over SSH stdin, never in argv or files. Extra stdin for the scion command can
# be supplied with HUB_CLI_STDIN (e.g. a secret value), sent after the token.
hub_cli() {
  local tok args z i=1 rc=0
  z="$(resolve_zone)"
  tok="$(gcloud auth print-identity-token 2>/dev/null)"
  printf -v args '%q ' "$@"
  while true; do
    rc=0
    { printf '%s\n' "${tok}"; if [[ -n "${HUB_CLI_STDIN:-}" ]]; then printf '%s' "${HUB_CLI_STDIN}"; fi; } | \
      gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="${z}" \
        --tunnel-through-iap --quiet --command="
          IFS= read -r SCION_HUB_TOKEN; export SCION_HUB_TOKEN
          export SCION_HUB_ENDPOINT=http://localhost:8080
          mkdir -p ~/acme-cli && cd ~/acme-cli && scion ${args}" && return 0 || rc=$?
    [[ "${rc}" -eq 255 && "${i}" -lt 3 ]] || return "${rc}"
    sleep "$(( 2 * i ))"
    (( i++ ))
  done
}

export PROJECT_ID REGION HUB_NAME HUB_PROJECT SANDBOX_PROJECT SANDBOX_REGION FACTORY_PREFIX BUDGET_NAME VM_NAME VM_SA PROXY_SVC
export REPO_ROOT FOLDER_ID BILLING_ACCOUNT SCION_SRC SCION_VERSION CONFIG_FILE LOCAL_ENV_FILE
export ORG_DOMAIN WEB_DOMAINS ADMIN_EMAIL PO_EMAIL DEFAULT_USER_ROLE GITHUB_REPO BUILD_PROJECT EVAL_PROJECT HUB_PROJECT_SLUG BUILD_BRANCH EVAL_BRANCH
export SERVING_SERVICE DASHBOARD_SERVICE DEPLOYER_SA_NAME RUNTIME_SA_NAME DEPLOYER_SA RUNTIME_SA AGENT_SA
export AGENT_TEAM_SHA IMPECCABLE_SHA BUDGET_USD HEARTBEAT_CRON TICK_CRON WATCHDOG_CRON DEPLOY_ZONE GCLOUD_CLIENT_ID
