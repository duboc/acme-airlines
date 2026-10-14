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
# Usage: infra/50-web-access.sh            # apply WEB_DOMAINS from infra/factory.local.env
#        WEB_DOMAINS="corp.example.com partner.example.com" DEFAULT_USER_ROLE=member infra/50-web-access.sh
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

DEFAULT_USER_ROLE="${DEFAULT_USER_ROLE:-viewer}"
SETTINGS=/home/scion/.scion/settings.yaml
BEGIN="# BEGIN acme-web-access (infra/50-web-access.sh)"
END="# END acme-web-access"

echo "==> IAP: grant roles/iap.httpsResourceAccessor on all deployed IAP Cloud Run services to: ${WEB_DOMAINS}"
read -ra DOMS <<<"${WEB_DOMAINS}"
for svc in "${PROXY_SVC}" "${FACTORY_PREFIX}-observatory" "${FACTORY_PREFIX}-irrops-demo"; do
  if ! gcloud run services describe "${svc}" --region="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "  (${svc} not deployed yet — skipping)"
    continue
  fi
  echo "  -> ${svc}"
  for d in "${DOMS[@]}"; do
    gcloud iap web add-iam-policy-binding --project="${PROJECT_ID}" \
      --resource-type=cloud-run --service="${svc}" --region="${REGION}" \
      --member="domain:${d}" --role=roles/iap.httpsResourceAccessor --condition=None --format=none
  done
  gcloud iap web get-iam-policy --project="${PROJECT_ID}" --resource-type=cloud-run \
    --service="${svc}" --region="${REGION}" --format='value(bindings)'
done

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
  # Ensure hub_settings 'access' section stays origin='seeded' so settings.yaml always wins on restart
  sudo sqlite3 /home/scion/.scion/hub.db \"UPDATE hub_settings SET origin='seeded', updated_by='seed' WHERE section='access';\" 2>/dev/null || true
  sudo systemctl restart scion-hub
  for i in \$(seq 1 30); do curl -sf http://localhost:8080/healthz >/dev/null && break; sleep 2; done
  curl -sf http://localhost:8080/healthz >/dev/null && echo 'hub healthy'
"
