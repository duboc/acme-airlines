#!/usr/bin/env bash
# Task 4 — Vertex AI (global) access for agents + the Claude harness image.
# Idempotent.
#
# - Ensures the hub VM service account has roles/aiplatform.user (deploy.sh
#   already grants it; re-asserted here so the dependency is explicit).
# - Builds localhost/scion/scion-claude:latest on the VM from the same scion
#   source checkout deploy.sh used for the antigravity image (/opt/scion-source).
# - Prints the hub-scoped GOOGLE_CLOUD_* env vars agents receive (must be global).
#
# Manual prerequisite (human): enable the Claude models in Vertex AI Model
# Garden for this project — see docs/scion/01-scion-and-gcp-setup.md.
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

echo "==> Ensuring roles/aiplatform.user for ${VM_SA}"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${VM_SA}" --role=roles/aiplatform.user \
  --condition=None --format=none

echo "==> Building scion-claude image on the VM (skipped if present)"
vm_ssh '
  set -euo pipefail
  if sudo docker image inspect localhost/scion/scion-claude:latest >/dev/null 2>&1; then
    echo "scion-claude already built"; exit 0
  fi
  cd /opt/scion-source
  sudo docker build -t scion-claude:latest \
    --build-arg BASE_IMAGE=scion-base:latest \
    -f harnesses/claude/Dockerfile harnesses/claude/
  sudo docker tag scion-claude:latest localhost/scion/scion-claude:latest
  sudo docker images | grep -E "localhost/scion/"
'

echo "==> Hub-scoped GOOGLE_CLOUD_* env vars (agents inherit these)"
vm_ssh "sudo sqlite3 /home/scion/.scion/hub.db \
  \"SELECT key, value FROM env_vars WHERE key LIKE 'GOOGLE_CLOUD_%';\"" || \
  echo "(could not read env vars table; check in the Hub admin UI)"
