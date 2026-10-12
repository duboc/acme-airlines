#!/usr/bin/env bash
# Task 3 — deploy the Scion Hub + runtime broker on a single GCE VM using
# Scion's supported single-node-vm deploy script, headlessly. Idempotent:
# deploy.sh converges on re-run.
#
# deploy.sh is taken from a clean clone of GoogleCloudPlatform/scion at the
# pinned release tag ($SCION_VERSION), so the script matches the binary it
# installs on the VM.
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

if [[ ! -d "${SCION_SRC}/.git" ]]; then
  echo "==> Cloning scion ${SCION_VERSION} into ${SCION_SRC}"
  git clone -q --depth 1 --branch "${SCION_VERSION}" \
    https://github.com/GoogleCloudPlatform/scion.git "${SCION_SRC}"
fi

mkdir -p "${INFRA_DIR}/logs"

# Local patch: deploy.sh always uses the region's first zone (us-central1-c),
# which was stocked out of n2-standard-16 on 2026-10-09. Make the zone
# selection honour DEPLOY_ZONE (default us-central1-a). Idempotent; only the
# zone-selection line in the cached clone is touched.
DEPLOY_ZONE="${DEPLOY_ZONE:-${REGION}-a}"   # set in factory.env
export DEPLOY_ZONE
DEPLOY_SH="${SCION_SRC}/scripts/single-node-vm/deploy.sh"
if ! grep -q 'DEPLOY_ZONE' "${DEPLOY_SH}"; then
  echo "==> Patching deploy.sh zone selection to honour DEPLOY_ZONE"
  perl -0pi -e 's/^ZONE="\$\(gcloud compute zones list \\\n/ZONE="\${DEPLOY_ZONE:-}"\n[[ -n "\$ZONE" ]] || ZONE="\$(gcloud compute zones list \\\n/m' "${DEPLOY_SH}"
  # shellcheck disable=SC2016  # literal match on the patched line
  grep -q 'ZONE="${DEPLOY_ZONE:-}"' "${DEPLOY_SH}" || { echo "patch failed" >&2; exit 1; }
fi
echo "==> Target zone: ${DEPLOY_ZONE}"
LOG="${INFRA_DIR}/logs/10-deploy-hub-$(date +%Y%m%d-%H%M%S).log"
echo "==> Running deploy.sh ${SCION_VERSION} (log: ${LOG})"
cd "${SCION_SRC}"
# </dev/null forces fully headless mode: missing config fields error out
# instead of blocking on a prompt.
./scripts/single-node-vm/deploy.sh --version "${SCION_VERSION}" --config "${CONFIG_FILE}" \
  </dev/null 2>&1 | tee "${LOG}"
