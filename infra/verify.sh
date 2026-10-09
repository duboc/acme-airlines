#!/usr/bin/env bash
# Verification harness for the acme-airlines factory.
#
# Usage: infra/verify.sh <project|hub|vertex|claude-image|hubproject|smoke|all>
# Prints "PASS <check>" or "FAIL <check>: <reason>"; exits non-zero on failure.
set -uo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"

fail() { echo "FAIL $1: $2"; return 1; }
pass() { echo "PASS $1"; }

check_project() {
  gcloud projects describe "${PROJECT_ID}" --format='value(projectId)' >/dev/null 2>&1 \
    || { fail project "project ${PROJECT_ID} not found"; return 1; }
  [[ "$(gcloud billing projects describe "${PROJECT_ID}" --format='value(billingEnabled)' 2>/dev/null)" == "True" ]] \
    || { fail project "billing not enabled"; return 1; }
  local enabled svc
  enabled="$(gcloud services list --enabled --project="${PROJECT_ID}" --format='value(config.name)' 2>/dev/null)"
  for svc in compute run iap cloudbuild artifactregistry aiplatform iamcredentials logging monitoring; do
    grep -qx "${svc}.googleapis.com" <<<"${enabled}" || { fail project "API ${svc} not enabled"; return 1; }
  done
  gcloud compute networks subnets describe default --region="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1 \
    || { fail project "subnet default missing in ${REGION}"; return 1; }
  pass project
}
check_hub() {
  local status url code
  status="$(gcloud compute instances describe "${VM_NAME}" --zone="$(resolve_zone)" \
             --project="${PROJECT_ID}" --format='value(status)' 2>/dev/null)"
  [[ "${status}" == "RUNNING" ]] || { fail hub "VM ${VM_NAME} status='${status}'"; return 1; }
  vm_ssh "systemctl is-active --quiet scion-hub && curl -sf http://localhost:8080/healthz >/dev/null" >/dev/null 2>&1 \
    || { fail hub "scion-hub service not active or /healthz failing"; return 1; }
  vm_ssh "sudo docker image inspect localhost/scion/scion-antigravity:latest >/dev/null" >/dev/null 2>&1 \
    || { fail hub "image localhost/scion/scion-antigravity:latest missing"; return 1; }
  url="$(gcloud run services describe "${PROXY_SVC}" --region="${REGION}" --project="${PROJECT_ID}" \
          --format='value(status.url)' 2>/dev/null)"
  [[ -n "${url}" ]] || { fail hub "Cloud Run ${PROXY_SVC} not found"; return 1; }
  code="$(curl -s -o /dev/null -w '%{http_code}' "${url}/")"
  [[ "${code}" =~ ^(302|401|403)$ ]] || { fail hub "proxy ${url} returned ${code} unauthenticated (IAP not enforcing?)"; return 1; }
  pass hub
}
check_vertex() {
  # Runs on the VM using the VM service account's metadata-server token —
  # the same credentials agents use. Global endpoint only.
  local base="https://aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/global/publishers"
  local out
  out="$(vm_ssh "
    T=\$(curl -s -H 'Metadata-Flavor: Google' \
      http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"access_token\"])')
    code=\$(curl -s -o /tmp/vx.json -w '%{http_code}' -X POST -H \"Authorization: Bearer \$T\" \
      -H 'Content-Type: application/json' \
      '${base}/google/models/${GEMINI_TEST_MODEL}:generateContent' \
      -d '{\"contents\":[{\"role\":\"user\",\"parts\":[{\"text\":\"Say ok\"}]}]}')
    echo \"gemini ${GEMINI_TEST_MODEL} \$code\"; [ \"\$code\" = 200 ] || head -c 300 /tmp/vx.json
    for m in ${CLAUDE_TEST_MODELS}; do
      code=\$(curl -s -o /tmp/vx.json -w '%{http_code}' -X POST -H \"Authorization: Bearer \$T\" \
        -H 'Content-Type: application/json' \
        '${base}/anthropic/models/'\$m':rawPredict' \
        -d '{\"anthropic_version\":\"vertex-2023-10-16\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Say ok\"}]}')
      echo \"claude \$m \$code\"; [ \"\$code\" = 200 ] || head -c 300 /tmp/vx.json
    done
  " 2>/dev/null)"
  echo "${out//$'\n'/$'\n'    }" | sed '1s/^/    /'
  if grep -qE '^(gemini|claude) \S+ [0-9]+$' <<<"${out}" && ! grep -E '^(gemini|claude) \S+ [0-9]+$' <<<"${out}" | grep -qv ' 200$'; then
    pass vertex
  else
    fail vertex "one or more model calls did not return 200 (see above)"
  fi
}
check_claude_image() {
  if vm_ssh "sudo docker image inspect localhost/scion/scion-claude:latest >/dev/null" >/dev/null 2>&1; then
    pass claude-image
  else
    fail claude-image "image localhost/scion/scion-claude:latest missing"
  fi
}
check_hubproject()   { fail hubproject "not implemented"; }
check_smoke()        { fail smoke "not implemented"; }

run() {
  case "$1" in
    project)      check_project ;;
    hub)          check_hub ;;
    vertex)       check_vertex ;;
    claude-image) check_claude_image ;;
    hubproject)   check_hubproject ;;
    smoke)        check_smoke ;;
    all)
      local rc=0
      for c in project hub vertex claude-image hubproject smoke; do run "$c" || rc=1; done
      return "$rc" ;;
    *) echo "usage: $0 <project|hub|vertex|claude-image|hubproject|smoke|all>" >&2; return 2 ;;
  esac
}

run "${1:-all}"
