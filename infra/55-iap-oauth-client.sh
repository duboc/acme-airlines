#!/usr/bin/env bash
# Configure a CUSTOM OAuth client on IAP for the hub proxy and the Observatory.
#
# Why: IAP's Google-managed OAuth client (what `gcloud run deploy --iap` uses)
# only admits users inside the project's organization (duboc.altostrat.com).
# External domains such as google.com need a custom OAuth client with an
# External consent screen (Cloud IAP docs: "Customize OAuth configuration").
#
# Prereq (console, cannot be scripted): Google Auth Platform -> Branding/Audience
# = External + In production; Clients -> Web application with redirect URI
#   https://iap.googleapis.com/v1/oauth/clientIds/<CLIENT_ID>:handleRedirect
#
# Usage (secret via stdin, never argv):
#   read -rs SECRET && printf '%s' "$SECRET" | IAP_CLIENT_ID=<id>.apps.googleusercontent.com infra/55-iap-oauth-client.sh; unset SECRET
#   infra/55-iap-oauth-client.sh --reapply     # re-apply from Secret Manager (e.g. after a re-deploy)
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"

P="${PROJECT_ID}"; R="${REGION}"
SERVICES=(scion-hub-acme-iap-proxy acme-observatory acme-irrops-demo)
ID_SECRET="acme-iap-oauth-client-id"; SECRET_SECRET="acme-iap-oauth-client-secret"

ensure_secret() { gcloud secrets describe "$1" --project="${P}" >/dev/null 2>&1 || \
  gcloud secrets create "$1" --project="${P}" --replication-policy=automatic; }

if [[ "${1:-}" != "--reapply" ]]; then
  : "${IAP_CLIENT_ID:?set IAP_CLIENT_ID=<id>.apps.googleusercontent.com}"
  [[ "${IAP_CLIENT_ID}" == *.apps.googleusercontent.com ]] || { echo "IAP_CLIENT_ID looks wrong" >&2; exit 1; }
  SECRET="$(cat)"; [[ -n "${SECRET}" ]] || { echo "client secret expected on stdin" >&2; exit 1; }
  ensure_secret "${ID_SECRET}"; ensure_secret "${SECRET_SECRET}"
  printf '%s' "${IAP_CLIENT_ID}" | gcloud secrets versions add "${ID_SECRET}" --project="${P}" --data-file=- --format=none
  printf '%s' "${SECRET}" | gcloud secrets versions add "${SECRET_SECRET}" --project="${P}" --data-file=- --format=none
  unset SECRET
fi

CID="$(gcloud secrets versions access latest --secret="${ID_SECRET}" --project="${P}")"
umask 077; TMP="$(mktemp)"; trap 'rm -f "${TMP}"' EXIT
{
  echo "access_settings:"
  echo "  oauth_settings:"
  echo "    client_id: \"${CID}\""
  printf '    client_secret: "%s"\n' "$(gcloud secrets versions access latest --secret="${SECRET_SECRET}" --project="${P}")"
} > "${TMP}"

for s in "${SERVICES[@]}"; do
  if ! gcloud run services describe "${s}" --region="${R}" --project="${P}" >/dev/null 2>&1; then
    echo "==> skipping ${s} (service not created yet)"; continue
  fi
  echo "==> IAP custom OAuth client on ${s}"
  gcloud iap settings set "${TMP}" --project="${P}" --resource-type=cloud-run --service="${s}" --region="${R}" --format=none
done
echo "done (client ${CID})"
