#!/usr/bin/env bash
# Verification harness for the acme-airlines factory.
#
# Usage: infra/verify.sh <project|hub|vertex|claude-image|hubproject|smoke|team|lead|topology|cost|skills|loop|budget|observatory|iap-oauth|irrops-demo|all>
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

# topology_ok <rows> <min-children>
# rows: one "name|id|ancestry-json" line per live agent. Passes when acme-lead
# exists, every other acme-* agent's ancestry chain contains the lead's id
# (child or deeper, e.g. via an eng-manager), and at least <min-children> such
# descendants exist. Non-acme agents are ignored.
topology_ok() {
  local rows="$1" min="${2:-0}" lead_id name anc n=0 bad=""
  lead_id="$(awk -F'|' '$1=="acme-lead"{print $2}' <<<"${rows}")"
  [[ -n "${lead_id}" ]] || { echo "no live acme-lead"; return 1; }
  while IFS='|' read -r name _ anc; do
    [[ "${name}" == acme-* && "${name}" != "acme-lead" ]] || continue
    if [[ "${anc}" == *"\"${lead_id}\""* ]]; then n=$((n+1)); else bad+=" ${name}"; fi
  done <<<"${rows}"
  [[ -z "${bad}" ]] || { echo "not under acme-lead:${bad}"; return 1; }
  (( n >= min )) || { echo "only ${n} agents under acme-lead (want >= ${min})"; return 1; }
  echo "${n} agents under acme-lead"
}

# Addendum-B model mapping: role -> "harness model".
declare -A ACME_MODEL=(
  [acme-lead]="claude large" [acme-eng-manager]="claude large" [acme-architect]="claude large"
  [acme-backend]="claude medium" [acme-frontend]="claude medium" [acme-qa]="claude medium"
  [acme-reviewer]="antigravity medium" [acme-security-auditor]="antigravity medium"
  [acme-investigator]="antigravity medium" [acme-researcher]="antigravity medium"
  [acme-qa-tester]="antigravity medium" [acme-doc-writer]="antigravity medium"
  [acme-release-notes]="antigravity medium"
)
_yaml_val() { awk -F': *' -v k="$1" '$1==k{gsub(/"/,"",$2); print $2; exit}' "$2"; }

# models_ok <templates-dir>: each acme-* template's harness/model matches ACME_MODEL.
models_ok() {
  local dir="$1" f t want got n=0 bad=""
  for f in "${dir}"/acme-*/scion-agent.yaml; do
    [[ -f "${f}" ]] || continue
    t="$(basename "$(dirname "${f}")")"; want="${ACME_MODEL[${t}]:-}"
    got="$(_yaml_val default_harness_config "${f}") $(_yaml_val model "${f}")"
    n=$((n+1)); [[ -z "${want}" || "${got}" == "${want}" ]] || bad+=" ${t}(${got}≠${want})"
  done
  (( n > 0 )) || { echo "no templates in ${dir}"; return 1; }
  [[ -z "${bad}" ]] || { echo "model mismatch:${bad}"; return 1; }
  echo "${n} templates match the model mapping"
}

# caps_ok <templates-dir>: every acme-* template except acme-lead sets max_duration and max_turns.
caps_ok() {
  local dir="$1" f t n=0 bad=""
  for f in "${dir}"/acme-*/scion-agent.yaml; do
    [[ -f "${f}" ]] || continue
    t="$(basename "$(dirname "${f}")")"; n=$((n+1))
    [[ "${t}" == "acme-lead" ]] && continue
    [[ -n "$(_yaml_val max_duration "${f}")" && -n "$(_yaml_val max_turns "${f}")" ]] || bad+=" ${t}"
  done
  (( n > 0 )) || { echo "no templates in ${dir}"; return 1; }
  [[ -z "${bad}" ]] || { echo "missing caps:${bad}"; return 1; }
  echo "caps present on all non-lead templates"
}

# cron_interval_ok <cron> <min-minutes>: the minute field fires at most every
# <min-minutes> minutes ("*/N", a single minute, or a comma list whose smallest
# gap, including wrap-around, is >= min).
cron_interval_ok() {
  local cron="$1" min="$2" m
  read -r m _ <<<"${cron}"
  [[ -n "${m}" ]] || return 1
  if [[ "${m}" =~ ^\*/([0-9]+)$ ]]; then (( BASH_REMATCH[1] >= min )); return; fi
  [[ "${m}" =~ ^[0-9]+(,[0-9]+)*$ ]] || return 1
  local -a v; IFS=',' read -ra v <<<"${m}"
  (( ${#v[@]} == 1 )) && return 0
  local i gap smallest=60
  for ((i=0; i<${#v[@]}; i++)); do
    if (( i+1 < ${#v[@]} )); then gap=$(( v[i+1] - v[i] )); else gap=$(( 60 - v[i] + v[0] )); fi
    (( gap < smallest )) && smallest=${gap}
  done
  (( smallest >= min ))
}

# gh_var <NAME>: repo variable exists (retries: GitHub's API lags a few seconds after `gh variable set`).
# Capture first, then grep: `gh … | grep -q` under pipefail fails when grep exits early and gh gets SIGPIPE.
gh_var() {
  local i out
  for i in 1 2 3 4; do
    out="$(gh variable list -R "${GITHUB_REPO}" 2>/dev/null)"
    grep -q "^$1[[:space:]]" <<<"${out}" && return 0
    sleep $((i*3))
  done
  return 1
}

# engines_ok <reasoningEngines-list-json>: exactly one acme-customer-agent and one
# acme-sales-agent, both running as acme-agent-engine, with no reserved GOOGLE_CLOUD_* env.
engines_ok() {
  local j="$1" sa="acme-agent-engine@${PROJECT_ID:-acme-airlines-factory}.iam.gserviceaccount.com" n bad
  for n in acme-customer-agent acme-sales-agent; do
    [[ "$(jq --arg n "${n}" '[.reasoningEngines[]? | select(.displayName==$n)] | length' <<<"${j}")" == 1 ]] \
      || { echo "expected exactly one engine '${n}'"; return 1; }
  done
  bad="$(jq -r --arg sa "${sa}" '[.reasoningEngines[]? | select(.displayName|startswith("acme-"))
          | select((.spec.serviceAccount // "") != $sa) | .displayName] | join(",")' <<<"${j}")"
  [[ -z "${bad}" ]] || { echo "engines not running as acme-agent-engine: ${bad}"; return 1; }
  # Reserved platform-injected names (rejected/overridden by Agent Engine). Documented settings such
  # as GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY are allowed.
  bad="$(jq -r '[.reasoningEngines[]? | .spec.deploymentSpec.env[]? | .name
          | select(test("^GOOGLE_CLOUD_(PROJECT|LOCATION|REGION|QUOTA_PROJECT)$"))] | join(",")' <<<"${j}")"
  [[ -z "${bad}" ]] || { echo "reserved env vars on engines: ${bad}"; return 1; }
  echo "engines: $(jq -r '[.reasoningEngines[]? | .displayName] | join(", ")' <<<"${j}")"
}

# agent_traces_ok <cloudtrace-v1-list-json>: at least one trace containing an ADK agent span
# (invoke_agent / call_llm / execute_tool), as opposed to plain Cloud Run request spans.
agent_traces_ok() {
  local n
  n="$(jq '[.traces[]? | select([.spans[]?.name] | any(test("^(invoke_agent|call_llm|execute_tool|agent_run)")))] | length' <<<"$1")"
  (( n > 0 )) || { echo "no ADK agent spans"; return 1; }
  echo "${n} traces with agent spans"
}

_gapi() {  # _gapi <url>: GET with the operator's gcloud token
  curl -s -H "Authorization: Bearer $(gcloud auth print-access-token 2>/dev/null)" \
       -H "x-goog-user-project: ${PROJECT_ID}" "$1"
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
    SELECT 'project', p.slug FROM projects p WHERE p.git_remote LIKE '%${GITHUB_REPO}%';
    SELECT 'secret', s.key FROM secrets s JOIN projects p ON s.scope_id = p.id
      WHERE s.scope = 'project' AND s.key = 'GITHUB_TOKEN' AND p.git_remote LIKE '%${GITHUB_REPO}%';\"" 2>/dev/null)"
  grep -q '^project|' <<<"${out}" || { fail hubproject "no hub project with remote ${GITHUB_REPO}"; return 1; }
  grep -q '^secret|GITHUB_TOKEN' <<<"${out}" || { fail hubproject "project secret GITHUB_TOKEN not set"; return 1; }
  pass hubproject
}
check_smoke() {
  local titles a
  titles="$(gh pr list -R "${GITHUB_REPO}" --state all --search 'smoke: in:title' --json title -q '.[].title' 2>/dev/null)"
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
check_topology() {
  # Standing roster: all live acme-* agents descend from acme-lead, and at least
  # TOPOLOGY_MIN (default 11, one per non-lead role) exist.
  local rows msg
  rows="$(printf '%s\n' ".separator '|'" \
    "SELECT name, id, ancestry FROM agents WHERE deleted_at IS NULL;" | \
    gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" \
      --tunnel-through-iap --quiet --command="sudo sqlite3 -readonly /home/scion/.scion/hub.db" 2>/dev/null)"
  if msg="$(topology_ok "${rows}" "${TOPOLOGY_MIN:-11}")"; then pass "topology (${msg})"; else fail topology "${msg}"; fi
}
check_cost() {
  # Committed templates follow the model mapping and carry caps.
  local m c
  m="$(models_ok "${REPO_ROOT}/factory/templates")" || { fail cost "${m}"; return 1; }
  c="$(caps_ok "${REPO_ROOT}/factory/templates")" || { fail cost "${c}"; return 1; }
  pass "cost (${m}; ${c})"
}
check_skills() {
  # The hub-stored acme-frontend template carries a template-mounted impeccable
  # skill (skills/impeccable/SKILL.md with "name: impeccable"), and no
  # unresolvable gh:// nested-path URI. (A URI string alone is not proof: the
  # hub rejected gh://owner/repo/.claude/skills/impeccable at provision time.)
  local out
  out="$(vm_ssh "sudo grep -rls '^name: impeccable' /home/scion/.scion --include=SKILL.md | grep '/acme-frontend/' | head -1; \
                 sudo grep -rls 'gh://pbakaus/impeccable/' /home/scion/.scion --include=scion-agent.yaml | grep '/acme-frontend/' | head -1 | sed 's/^/BADURI:/'" 2>/dev/null)"
  if grep -q '^BADURI:' <<<"${out}"; then fail skills "hub acme-frontend still references a nested gh:// impeccable URI"; return 1; fi
  if grep -q 'SKILL.md' <<<"${out}"; then pass skills; else fail skills "impeccable SKILL.md not mounted in hub-stored acme-frontend template"; fi
}
check_loop() {
  # The recurring TICK schedule exists, targets acme-lead, is active, and fires at most every 30 min.
  local row cron status payload
  row="$(printf '%s\n' ".separator '|'" "SELECT cron_expr, status, payload FROM schedules WHERE name='acme-factory-tick';" | \
    gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet \
      --command="sudo sqlite3 -readonly /home/scion/.scion/hub.db" 2>/dev/null)"
  [[ -n "${row}" ]] || { fail loop "schedule acme-factory-tick not found"; return 1; }
  IFS='|' read -r cron status payload <<<"${row}"
  [[ "${status}" == "active" ]] || { fail loop "schedule status=${status} (paused? run infra/60-factory.sh resume)"; return 1; }
  [[ "${payload}" == *acme-lead* ]] || { fail loop "schedule does not target acme-lead"; return 1; }
  cron_interval_ok "${cron}" 30 || { fail loop "cron '${cron}' fires more often than every 30 min"; return 1; }
  pass "loop (${cron}, active)"
}
check_budget() {
  # Budget exists with a 100% threshold wired to the guard topic, and the guard service is deployed.
  local b
  b="$(gcloud billing budgets list --billing-account="${BILLING_ACCOUNT}" \
        --filter='displayName=acme-airlines-factory-monthly' --format=json 2>/dev/null)"
  [[ "$(jq 'length' <<<"${b:-[]}")" -ge 1 ]] || { fail budget "budget acme-airlines-factory-monthly not found"; return 1; }
  jq -e '.[0].thresholdRules | map(.thresholdPercent) | index(1)' <<<"${b}" >/dev/null \
    || { fail budget "no 100% threshold"; return 1; }
  jq -e '.[0].notificationsRule.pubsubTopic | endswith("/topics/acme-budget-alerts")' <<<"${b}" >/dev/null \
    || { fail budget "budget not wired to topic acme-budget-alerts"; return 1; }
  local svc
  svc="$(gcloud run services describe acme-budget-guard --region="${REGION}" --project="${PROJECT_ID}" --format=json 2>/dev/null)"
  [[ -n "${svc}" ]] || { fail budget "Cloud Run acme-budget-guard missing"; return 1; }
  jq -e '[.spec.template.spec.containers[0].env[]? | select(.name=="SCION_HUB_TOKEN") | .valueFrom.secretKeyRef.name] | index("acme-guard-hub-token")' <<<"${svc}" >/dev/null \
    || { fail budget "guard has no hub token secret (old VM-stopping guard?)"; return 1; }
  # Product-owner rule: never stop the VM -> the guard SA must hold no role on the instance.
  if gcloud compute instances get-iam-policy "${VM_NAME}" --zone="$(resolve_zone)" --project="${PROJECT_ID}" --format=json 2>/dev/null \
       | jq -e '[.bindings[]?.members[]?] | index("serviceAccount:acme-budget-guard@'"${PROJECT_ID}"'.iam.gserviceaccount.com")' >/dev/null; then
    fail budget "guard SA still has a role on the VM"; return 1
  fi
  pass "budget ($(jq -r '.[0].amount.specifiedAmount.units' <<<"${b}") USD, 50/90/100%, guard suspends agents, never stops VM)"
}
check_observatory() {
  # Platform for the Observatory workstream (addendum C) exists and is wired.
  local p="${PROJECT_ID}" r="${REGION}" sa missing=""
  for sa in acme-observatory acme-observatory-collector acme-observatory-deployer; do
    gcloud iam service-accounts describe "${sa}@${p}.iam.gserviceaccount.com" --project="${p}" >/dev/null 2>&1 || missing+=" sa:${sa}"
  done
  for ds in billing_export factory_ops; do
    bq --project_id="${p}" show --format=none "${p}:${ds}" >/dev/null 2>&1 || missing+=" dataset:${ds}"
  done
  gcloud iam workload-identity-pools providers describe github-actions --workload-identity-pool=github \
    --location=global --project="${p}" >/dev/null 2>&1 || missing+=" wif:github/github-actions"
  gcloud artifacts repositories describe observatory --location="${r}" --project="${p}" >/dev/null 2>&1 || missing+=" ar:observatory"
  gcloud secrets versions list acme-observatory-hub-token --project="${p}" --filter=state=ENABLED --format='value(name)' 2>/dev/null | grep -q . \
    || missing+=" secret:acme-observatory-hub-token"
  gcloud secrets describe acme-observatory-github-token --project="${p}" >/dev/null 2>&1 || missing+=" secret:acme-observatory-github-token"
  gcloud run services describe acme-observatory --region="${r}" --project="${p}" >/dev/null 2>&1 || missing+=" run:acme-observatory"
  gcloud run jobs describe acme-observatory-collector --region="${r}" --project="${p}" >/dev/null 2>&1 || missing+=" job:acme-observatory-collector"
  gcloud scheduler jobs describe acme-observatory-collect --location="${r}" --project="${p}" >/dev/null 2>&1 || missing+=" scheduler:acme-observatory-collect"
  gh_var OBSERVATORY_WIF_PROVIDER || missing+=" gh-var:OBSERVATORY_WIF_PROVIDER"
  gcloud projects get-iam-policy "${p}" --format=json 2>/dev/null \
    | jq -e --arg m "serviceAccount:acme-observatory@${p}.iam.gserviceaccount.com" \
        '.bindings[] | select(.role=="roles/cloudtrace.user") | .members | index($m)' >/dev/null \
    || missing+=" cloudtrace.user->acme-observatory"
  [[ -z "${missing}" ]] || { fail observatory "missing:${missing}"; return 1; }
  local note=""
  gcloud secrets versions list acme-observatory-github-token --project="${p}" --filter=state=ENABLED --format='value(name)' 2>/dev/null | grep -q . \
    || note=" (GitHub read token not yet added)"
  pass "observatory platform${note}"
}
check_irrops_demo() {
  # IRROPS demo platform (infra/90-irrops-demo-platform.sh): IAP-protected, single instance, fake LLM.
  local p="${PROJECT_ID}" r="${REGION}" svc=acme-irrops-demo missing="" d ann scaling
  gcloud iam service-accounts describe "${svc}@${p}.iam.gserviceaccount.com" --project="${p}" >/dev/null 2>&1 || missing+=" sa:${svc}"
  gcloud artifacts repositories describe irrops --location="${r}" --project="${p}" >/dev/null 2>&1 || missing+=" ar:irrops"
  d="$(gcloud run services describe "${svc}" --region="${r}" --project="${p}" --format=json 2>/dev/null)" || missing+=" run:${svc}"
  gh_var IRROPS_DEMO_SERVICE || missing+=" gh-var:IRROPS_DEMO_SERVICE"
  [[ -z "${missing}" ]] || { fail irrops-demo "missing:${missing}"; return 1; }
  ann="$(jq -r '.metadata.annotations["run.googleapis.com/iap-enabled"] // "false"' <<<"${d}")"
  scaling="$(jq -r '.spec.template.metadata.annotations["autoscaling.knative.dev/maxScale"] // ""' <<<"${d}")"
  [[ "${ann}" == "true" ]] || { fail irrops-demo "IAP is not enabled on ${svc}"; return 1; }
  [[ "${scaling}" == "1" ]] || { fail irrops-demo "max instances is '${scaling}', want 1 (in-memory demo state)"; return 1; }
  # Never public: allUsers/allAuthenticatedUsers must not hold run.invoker.
  if gcloud run services get-iam-policy "${svc}" --region="${r}" --project="${p}" --format=json 2>/dev/null \
       | jq -e '[.bindings[]?.members[]?] | any(. == "allUsers" or . == "allAuthenticatedUsers")' >/dev/null; then
    fail irrops-demo "${svc} is publicly invokable"; return 1
  fi
  # The runtime SA may hold exactly one project role: roles/aiplatform.user, so it can
  # query the Agent Engine engines (addendum D). Anything else is a least-privilege violation.
  local extra
  extra="$(gcloud projects get-iam-policy "${p}" --format=json 2>/dev/null \
    | jq -r --arg m "serviceAccount:${svc}@${p}.iam.gserviceaccount.com" \
        '[.bindings[]? | select(.members | index($m)) | .role | select(. != "roles/aiplatform.user")] | join(",")')"
  if [[ -n "${extra}" ]]; then
    fail irrops-demo "${svc} SA holds unexpected project role(s): ${extra}"; return 1
  fi
  pass "irrops-demo ($(jq -r '.status.url' <<<"${d}"))"
}
check_iap_oauth() {
  # External domains (google.com) need a CUSTOM OAuth client on IAP; the
  # Google-managed client only admits users inside the project's organization.
  local s cid bad=""
  for s in scion-hub-acme-iap-proxy acme-observatory; do
    cid="$(gcloud iap settings get --project="${PROJECT_ID}" --resource-type=cloud-run --service="${s}" \
            --region="${REGION}" --format='value(accessSettings.oauthSettings.clientId)' 2>/dev/null)"
    [[ "${cid}" == *.apps.googleusercontent.com ]] || bad+=" ${s}"
  done
  [[ -z "${bad}" ]] || { fail iap-oauth "no custom OAuth client on:${bad} (external domains such as google.com are refused)"; return 1; }
  pass iap-oauth
}
check_agent_engine() {
  # Platform for running the IRROPS ADK agents on Vertex AI Agent Engine (addendum D).
  local p="${PROJECT_ID}" missing="" pol rt="acme-agent-engine@${PROJECT_ID}.iam.gserviceaccount.com"
  gcloud iam service-accounts describe "${rt}" --project="${p}" >/dev/null 2>&1 || missing+=" sa:acme-agent-engine"
  gcloud storage buckets describe "gs://${p}-agent-engine" --format='value(name)' >/dev/null 2>&1 || missing+=" bucket:${p}-agent-engine"
  pol="$(gcloud projects get-iam-policy "${p}" --format=json 2>/dev/null)"
  _has() { jq -e --arg r "$1" --arg m "$2" '.bindings[] | select(.role==$r) | .members | index($m)' <<<"${pol}" >/dev/null; }
  local role
  for role in roles/aiplatform.user roles/cloudtrace.agent roles/logging.logWriter roles/monitoring.metricWriter; do
    _has "${role}" "serviceAccount:${rt}" || missing+=" ${role}->acme-agent-engine"
  done
  _has roles/aiplatform.user "serviceAccount:acme-observatory-deployer@${p}.iam.gserviceaccount.com" || missing+=" aiplatform.user->deployer"
  _has roles/logging.viewer "serviceAccount:acme-observatory-deployer@${p}.iam.gserviceaccount.com" || missing+=" logging.viewer->deployer"
  _has roles/cloudtrace.user "serviceAccount:acme-observatory-deployer@${p}.iam.gserviceaccount.com" || missing+=" cloudtrace.user->deployer"
  _has roles/aiplatform.user "serviceAccount:acme-irrops-demo@${p}.iam.gserviceaccount.com" || missing+=" aiplatform.user->irrops-demo"
  gcloud iam service-accounts get-iam-policy "${rt}" --project="${p}" --format=json 2>/dev/null \
    | jq -e '.bindings[]? | select(.role=="roles/iam.serviceAccountUser") | .members | index("serviceAccount:acme-observatory-deployer@'"${p}"'.iam.gserviceaccount.com")' >/dev/null \
    || missing+=" deployer-actAs-acme-agent-engine"
  # Bucket-level: the Vertex SDK calls storage.buckets.get before uploading (#186).
  local bpol
  bpol="$(gcloud storage buckets get-iam-policy "gs://${p}-agent-engine" --format=json 2>/dev/null)"
  jq -e --arg m "serviceAccount:acme-observatory-deployer@${p}.iam.gserviceaccount.com" \
     '.bindings[] | select(.role=="roles/storage.legacyBucketReader") | .members | index($m)' <<<"${bpol}" >/dev/null \
    || missing+=" legacyBucketReader->deployer(bucket)"
  # Once the Reasoning Engine service agent exists, it must be able to mint tokens for the runtime SA.
  local re pnum
  pnum="$(gcloud projects describe "${p}" --format='value(projectNumber)')"
  re="service-${pnum}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
  if jq -e --arg m "serviceAccount:${re}" '.bindings[] | .members | index($m)' <<<"${bpol}" >/dev/null; then
    gcloud iam service-accounts get-iam-policy "${rt}" --project="${p}" --format=json 2>/dev/null \
      | jq -e --arg m "serviceAccount:${re}" '.bindings[]? | select(.role=="roles/iam.serviceAccountTokenCreator") | .members | index($m)' >/dev/null \
      || missing+=" tokenCreator(re-agent)->acme-agent-engine"
  fi
  gh_var AGENT_ENGINE_SA || missing+=" gh-var:AGENT_ENGINE_SA"
  [[ -z "${missing}" ]] || { fail agent-engine "missing:${missing}"; return 1; }
  pass agent-engine
}
check_engines() {
  local msg
  if msg="$(engines_ok "$(_gapi "https://${REGION}-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/reasoningEngines")")"; then
    pass "engines (${msg})"; else fail engines "${msg}"; fi
}
check_traces() {
  # Agent spans in Cloud Trace within the last 24h.
  local start msg
  start="$(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)"
  if msg="$(agent_traces_ok "$(_gapi "https://cloudtrace.googleapis.com/v1/projects/${PROJECT_ID}/traces?view=COMPLETE&pageSize=100&startTime=${start}")")"; then
    pass "traces (${msg})"; else fail traces "${msg} in the last 24h"; fi
}
check_demo_engine() {
  # The hosted demo is configured to drive the deployed engines.
  local env
  env="$(gcloud run services describe acme-irrops-demo --region="${REGION}" --project="${PROJECT_ID}" --format=json 2>/dev/null \
         | jq -r '[.spec.template.spec.containers[0].env[]? | "\(.name)=\(.value // "")"] | join(" ")')"
  [[ "${env}" == *"ENGINE_MODE=agent-engine"* ]] || { fail demo-engine "demo ENGINE_MODE is not agent-engine (${env})"; return 1; }
  pass "demo-engine"
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
    topology)     check_topology ;;
    cost)         check_cost ;;
    skills)       check_skills ;;
    loop)         check_loop ;;
    budget)       check_budget ;;
    observatory)  check_observatory ;;
    irrops-demo)  check_irrops_demo ;;
    iap-oauth)    check_iap_oauth ;;
    agent-engine) check_agent_engine ;;
    engines)      check_engines ;;
    traces)       check_traces ;;
    demo-engine)  check_demo_engine ;;
    all)
      local rc=0
      for c in project hub vertex claude-image hubproject smoke team lead topology cost skills loop budget observatory iap-oauth agent-engine; do run "$c" || rc=1; done
      return "$rc" ;;
    e2e)
      local rc=0
      for c in lead loop engines traces demo-engine; do run "$c" || rc=1; done
      return "$rc" ;;
    *) echo "usage: $0 <project|hub|vertex|claude-image|hubproject|smoke|team|lead|topology|cost|skills|loop|budget|observatory|iap-oauth|irrops-demo|all>" >&2; return 2 ;;
  esac
}

[[ -n "${VERIFY_LIB_ONLY:-}" ]] || run "${1:-all}"
