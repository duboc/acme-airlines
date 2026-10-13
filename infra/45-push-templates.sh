#!/usr/bin/env bash
# Push the role templates (factory/templates/acme-*) to the hub at global scope.
#
# Opens the temporary operator admin window (infra/30-hub-admin-access.sh) and always
# closes it on exit, also on error or Ctrl-C. Re-pushing an unchanged template is harmless.
# Running agents keep the template they started with; new and reincarnated agents
# (scion reincarnate <agent>) pick up the pushed version.
#
# Usage: infra/45-push-templates.sh                       # every acme-* template
#        infra/45-push-templates.sh acme-backend acme-qa  # only the named ones
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
cd "${REPO_ROOT}"

TEMPLATES=("$@")
if (( ${#TEMPLATES[@]} == 0 )); then
  for d in factory/templates/acme-*; do TEMPLATES+=("$(basename "${d}")"); done
fi
for t in "${TEMPLATES[@]}"; do
  [[ "${t}" =~ ^acme-[a-z-]+$ && -f "factory/templates/${t}/scion-agent.yaml" ]] \
    || { echo "not a template: ${t}" >&2; exit 1; }
done

bash "${INFRA_DIR}/30-hub-admin-access.sh" enable
trap 'bash "${INFRA_DIR}/30-hub-admin-access.sh" disable' EXIT

echo "==> Copying ${#TEMPLATES[@]} templates to the VM (~/.scion/templates/)"
# Replace (not merge) each copy so files deleted in the repo do not linger on the VM.
vm_ssh "mkdir -p ~/.scion/templates && cd ~/.scion/templates && rm -rf ${TEMPLATES[*]}"
gcloud compute scp --recurse "${TEMPLATES[@]/#/factory/templates/}" "${VM_NAME}:~/.scion/templates/" \
  --project="${PROJECT_ID}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet

for t in "${TEMPLATES[@]}"; do
  echo "==> templates push ${t} (global scope)"
  hub_cli --global templates push "${t}"
done
echo "done: pushed ${TEMPLATES[*]}"
