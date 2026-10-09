#!/usr/bin/env bash
# Verification harness for the acme-airlines factory.
#
# Usage: infra/verify.sh <project|hub|vertex|claude-image|hubproject|smoke|team|lead|all>
# Prints "PASS <check>" or "FAIL <check>: <reason>"; exits non-zero on failure.
set -uo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

fail() { echo "FAIL $1: $2"; return 1; }
pass() { echo "PASS $1"; }

# vertex_all_ok <probe-output>: true only if there is a gemini line and at
# least one claude line, and EVERY gemini/claude result line ends in " 200".
# A missing or empty HTTP code counts as a failure (no false PASS).
vertex_all_ok() {
  local lines
  lines="$(grep -E '^(gemini|claude) ' <<<"$1")"
  grep -q '^gemini ' <<<"${lines}" || return 1
  grep -q '^claude ' <<<"${lines}" || return 1
  ! grep -vE ' 200$' <<<"${lines}" | grep -q .
}

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
    echo \"gemini ${GEMINI_TEST_MODEL} \$code\"; [ \"\$code\" = 200 ] || { head -c 300 /tmp/vx.json; echo; }
    for m in ${CLAUDE_TEST_MODELS}; do
      code=\$(curl -s -o /tmp/vx.json -w '%{http_code}' -X POST -H \"Authorization: Bearer \$T\" \
        -H 'Content-Type: application/json' \
        '${base}/anthropic/models/'\$m':rawPredict' \
        -d '{\"anthropic_version\":\"vertex-2023-10-16\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Say ok\"}]}')
      echo \"claude \$m \$code\"; [ \"\$code\" = 200 ] || { head -c 300 /tmp/vx.json; echo; }
    done
  " 2>/dev/null)"
  echo "${out//$'\n'/$'\n'    }" | sed '1s/^/    /'
  if vertex_all_ok "${out}"; then
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
check_hubproject() {
  # Read-only query of the hub DB on the VM. Reads the secret's *name* only.
  local out
  out="$(vm_ssh "sudo sqlite3 -readonly /home/scion/.scion/hub.db \"
    SELECT 'project', p.slug FROM projects p WHERE p.git_remote LIKE '%duboc/acme-airlines%';
    SELECT 'secret', s.key FROM secrets s JOIN projects p ON s.scope_id = p.id
      WHERE s.scope = 'project' AND s.key = 'GITHUB_TOKEN' AND p.git_remote LIKE '%duboc/acme-airlines%';\"" 2>/dev/null)"
  grep -q '^project|' <<<"${out}" || { fail hubproject "no hub project with remote duboc/acme-airlines"; return 1; }
  grep -q '^secret|GITHUB_TOKEN' <<<"${out}" || { fail hubproject "project secret GITHUB_TOKEN not set"; return 1; }
  pass hubproject
}
check_smoke() {
  local titles a
  titles="$(gh pr list -R duboc/acme-airlines --state all --search 'smoke: in:title' --json title -q '.[].title' 2>/dev/null)"
  for a in smoke-gemini smoke-claude; do
    grep -q "smoke: ${a}" <<<"${titles}" || { fail smoke "no PR titled 'smoke: ${a}'"; return 1; }
  done
  pass smoke
}
check_team() {
  # Read-only: all 13 acme-* templates registered on the hub.
  local out t
  out="$(vm_ssh "sudo sqlite3 -readonly /home/scion/.scion/hub.db \"SELECT name FROM templates WHERE name LIKE 'acme-%';\"" 2>/dev/null)"
  for t in acme-lead acme-backend acme-frontend acme-qa acme-reviewer acme-architect acme-eng-manager \
           acme-security-auditor acme-investigator acme-researcher acme-qa-tester acme-doc-writer acme-release-notes; do
    grep -qx "${t}" <<<"${out}" || { fail team "template ${t} not registered on hub"; return 1; }
  done
  pass team
}
check_lead() {
  # The lead agent's container is running on the VM.
  vm_ssh "sudo docker ps --format '{{.Names}}' | grep -q 'acme-lead'" >/dev/null 2>&1 \
    || { fail lead "no running container for acme-lead"; return 1; }
  pass lead
}

run() {
  case "$1" in
    project)      check_project ;;
    hub)          check_hub ;;
    vertex)       check_vertex ;;
    claude-image) check_claude_image ;;
    hubproject)   check_hubproject ;;
    smoke)        check_smoke ;;
    team)         check_team ;;
    lead)         check_lead ;;
    all)
      local rc=0
      for c in project hub vertex claude-image hubproject smoke team lead; do run "$c" || rc=1; done
      return "$rc" ;;
    *) echo "usage: $0 <project|hub|vertex|claude-image|hubproject|smoke|team|lead|all>" >&2; return 2 ;;
  esac
}

[[ -n "${VERIFY_LIB_ONLY:-}" ]] || run "${1:-all}"
