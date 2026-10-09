#!/usr/bin/env bash
# Tear down the factory. Default: remove everything deploy.sh created (VM,
# Cloud Run proxy, service accounts, NAT/router, firewall rules) but keep the
# project. With --delete-project, also delete the whole GCP project
# (irreversible after the 30-day recovery window).
#
# Usage: infra/90-teardown.sh [--delete-project]
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"

echo "This will delete the Scion hub '${HUB_NAME}' in project ${PROJECT_ID}."
read -rp "Type the project ID to confirm: " ans
[[ "${ans}" == "${PROJECT_ID}" ]] || { echo "aborted"; exit 1; }

[[ -d "${SCION_SRC}/.git" ]] || git clone -q --depth 1 --branch "${SCION_VERSION}" \
  https://github.com/GoogleCloudPlatform/scion.git "${SCION_SRC}"
# deploy.sh --delete prompts for hub name/region and confirms again.
(cd "${SCION_SRC}" && ./scripts/single-node-vm/deploy.sh --delete --config "${CONFIG_FILE}")

if [[ "${1:-}" == "--delete-project" ]]; then
  read -rp "Also DELETE PROJECT ${PROJECT_ID}? Type 'delete ${PROJECT_ID}': " ans2
  [[ "${ans2}" == "delete ${PROJECT_ID}" ]] || { echo "project kept"; exit 0; }
  gcloud projects delete "${PROJECT_ID}" --quiet
fi
