#!/usr/bin/env bash
# Web (browser) access to the hub for whole domains. Idempotent.
#
# Two layers, both required for a user to get in:
#   1. IAP: roles/iap.httpsResourceAccessor on the Cloud Run proxy service for
#      domain:<d> (who may reach the hub at all).
#   2. Hub sign-in policy in settings.yaml (server.auth): user_access_mode
#      domain_restricted + authorized_domains (who the hub accepts), and the
#      default_user_role for new users. admin_emails stay admins.
#
# NOTE: deploy.sh rewrites settings.yaml, so re-run this script after any
# re-deploy (infra/10-deploy-hub.sh).
#
# Usage: infra/50-web-access.sh            # apply WEB_DOMAINS
#        WEB_DOMAINS="google.com duboc.altostrat.com" DEFAULT_USER_ROLE=member infra/50-web-access.sh
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

DEFAULT_USER_ROLE="${DEFAULT_USER_ROLE:-member}"
SETTINGS=/home/scion/.scion/settings.yaml
BEGIN="# BEGIN acme-web-access (infra/50-web-access.sh)"
END="# END acme-web-access"

echo "==> IAP: grant roles/iap.httpsResourceAccessor on ${PROXY_SVC} to: ${WEB_DOMAINS}"
read -ra DOMS <<<"${WEB_DOMAINS}"
for d in "${DOMS[@]}"; do
  gcloud iap web add-iam-policy-binding --project="${PROJECT_ID}" \
    --resource-type=cloud-run --service="${PROXY_SVC}" --region="${REGION}" \
    --member="domain:${d}" --role=roles/iap.httpsResourceAccessor --condition=None --format=none
done
gcloud iap web get-iam-policy --project="${PROJECT_ID}" --resource-type=cloud-run \
  --service="${PROXY_SVC}" --region="${REGION}" --format='value(bindings)'

read -ra DOMS <<<"${WEB_DOMAINS}"
domains_yaml="$(printf '"%s", ' "${DOMS[@]}")"; domains_yaml="[${domains_yaml%, }]"

echo "==> Hub sign-in policy: domain_restricted ${domains_yaml}, default_user_role=${DEFAULT_USER_ROLE}"
vm_ssh "
  set -euo pipefail
  sudo cp -a ${SETTINGS} ${SETTINGS}.bak-\$(date +%Y%m%d%H%M%S)
  # Remove a previous block, then insert a fresh one right after '  auth:'.
  sudo sed -i '/${BEGIN//\//\\/}/,/${END//\//\\/}/d' ${SETTINGS}
  sudo grep -q '^  auth:\$' ${SETTINGS} || { echo 'no server.auth block in settings.yaml' >&2; exit 1; }
  sudo sed -i '/^  auth:\$/a\\
    ${BEGIN}\\
    user_access_mode: domain_restricted\\
    authorized_domains: ${domains_yaml}\\
    default_user_role: ${DEFAULT_USER_ROLE}\\
    ${END}' ${SETTINGS}
  sudo sed -n '/^  auth:/,/^  [a-z_]*:\$/p' ${SETTINGS} | head -12
  sudo systemctl restart scion-hub
  for i in \$(seq 1 30); do curl -sf http://localhost:8080/healthz >/dev/null && break; sleep 2; done
  curl -sf http://localhost:8080/healthz >/dev/null && echo 'hub healthy'
"
