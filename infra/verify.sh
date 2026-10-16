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

# topology_ok <rows> [min-children] [lead-name]
# rows: one "name|id|ancestry-json" line per live agent. Passes when the project
# lead (<lead-name>, or lead / acme-lead by default) exists, every other project
# agent's ancestry chain contains the lead's id (child or deeper), and at least
# <min-children> (default 0) such descendants exist. smoke-* test agents are ignored.
topology_ok() {
  local rows="$1" min="${2:-0}" want_lead="${3:-}" lead_name="" lead_id="" name anc n=0 bad=""
  if [[ -n "${want_lead}" ]]; then
    lead_name="${want_lead}"
    lead_id="$(awk -F'|' -v l="${want_lead}" '$1==l{print $2; exit}' <<<"${rows}")"
  else
    for cand in lead acme-lead eval-lead; do
      lead_id="$(awk -F'|' -v l="${cand}" '$1==l{print $2; exit}' <<<"${rows}")"
      if [[ -n "${lead_id}" ]]; then lead_name="${cand}"; break; fi
    done
  fi
  [[ -n "${lead_id}" ]] || { echo "no live ${want_lead:-lead}"; return 1; }
  while IFS='|' read -r name _ anc _; do
    [[ -n "${name}" && "${name}" != "${lead_name}" && "${name}" != smoke-* ]] || continue
    if [[ "${anc}" == *"\"${lead_id}\""* ]]; then n=$((n+1)); else bad+=" ${name}"; fi
  done <<<"${rows}"
  [[ -z "${bad}" ]] || { echo "not under ${lead_name}:${bad}"; return 1; }
  (( n >= min )) || { echo "only ${n} agents under ${lead_name} (want >= ${min})"; return 1; }
  echo "${lead_name} + ${n} active workers under ${lead_name}"
}

# 12-template model mapping: role -> "harness model".
declare -A ACME_MODEL=(
  [lead]="claude large"
  [researcher]="antigravity large"
  [product-manager]="antigravity large"
  [backend]="claude medium"
  [frontend]="antigravity large"
  [reviewer]="antigravity large"
  [deployer]="claude medium"
  [eval-lead]="antigravity large"
  [factory-analyst]="antigravity large"
  [search-evaluator]="antigravity large"
  [ui-craftsman]="antigravity large"
  [eval-deployer]="antigravity large"
)
_yaml_val() { awk -F': *' -v k="$1" '$1==k{gsub(/"/,"",$2); print $2; exit}' "$2"; }

# models_ok <templates-dir>: each template's harness/model matches ACME_MODEL.
models_ok() {
  local dir="$1" f t want got n=0 bad=""
  for f in "${dir}"/*/scion-agent.yaml; do
    [[ -f "${f}" ]] || continue
    t="$(basename "$(dirname "${f}")")"
    [[ "${t}" == _* ]] && continue
    want="${ACME_MODEL[${t}]:-}"
    got="$(_yaml_val default_harness_config "${f}") $(_yaml_val model "${f}")"
    n=$((n+1)); [[ -n "${want}" && "${got}" == "${want}" ]] || bad+=" ${t}(${got}≠${want:-unknown})"
  done
  (( n > 0 )) || { echo "no templates in ${dir}"; return 1; }
  [[ -z "${bad}" ]] || { echo "model mismatch:${bad}"; return 1; }
  echo "${n} templates match the model mapping"
}

# caps_ok <templates-dir>: no template sets max_duration, max_turns or max_model_calls
# (standing agents run continuously without artificial turn/duration caps; budget alerts provide visibility).
caps_ok() {
  local dir="$1" f t n=0 bad=""
  for f in "${dir}"/*/scion-agent.yaml; do
    [[ -f "${f}" ]] || continue
    t="$(basename "$(dirname "${f}")")"
    [[ "${t}" == _* ]] && continue
    n=$((n+1))
    if [[ -n "$(_yaml_val max_duration "${f}")" || -n "$(_yaml_val max_turns "${f}")" \
       || -n "$(_yaml_val max_model_calls "${f}")" ]]; then
      bad+=" ${t}"
    fi
  done
  (( n > 0 )) || { echo "no templates in ${dir}"; return 1; }
  [[ -z "${bad}" ]] || { echo "unexpected caps on standing templates:${bad}"; return 1; }
  echo "no artificial caps on standing templates"
}

# eval_prompts_ok <templates-dir>: eval templates target the real ACME IRROPS application & Gemini model
# (VertexLlm, intent.py, outreach.py, scenarios.py, agent_engine_proof.py, ACME_DIAG, IrropsPanel/AgentEnginePanel/ModelsPanel)
# and never reference phantom modules like acme.evals.runner.
eval_prompts_ok() {
  local dir="$1" r f bad="" token
  for r in eval-lead search-evaluator factory-analyst ui-craftsman eval-deployer; do
    f="${dir}/${r}/system-prompt.md"
    [[ -f "${f}" ]] || { echo "missing ${r}/system-prompt.md"; return 1; }
    if grep -q "acme\.evals\.runner" "${f}"; then
      bad+=" ${r}(phantom:acme.evals.runner)"
    fi
  done
  for token in "VertexLlm" "intent.py" "outreach.py" "scenarios.py" "agent_engine_proof.py" "acme.llm.template_fallbacks" "STRUCTURAL"; do
    grep -q "${token}" "${dir}/search-evaluator/system-prompt.md" || bad+=" search-evaluator(missing:${token})"
  done
  for token in "ACME_DIAG" "acme.guardrail.decisions" "observatory/collector"; do
    grep -q "${token}" "${dir}/factory-analyst/system-prompt.md" || bad+=" factory-analyst(missing:${token})"
  done
  for token in "IrropsPanel.tsx" "AgentEnginePanel.tsx" "ModelsPanel.tsx"; do
    grep -q "${token}" "${dir}/ui-craftsman/system-prompt.md" || bad+=" ui-craftsman(missing:${token})"
  done
  for token in "VertexLlm" "agent_engine_proof.py" "IrropsPanel.tsx"; do
    grep -q "${token}" "${dir}/eval-lead/system-prompt.md" || bad+=" eval-lead(missing:${token})"
  done
  [[ -z "${bad}" ]] || { echo "generic/incomplete eval prompts:${bad}"; return 1; }
  echo "eval prompts specialized for ACME IRROPS app & Gemini model"
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

# agent_traces_ok <cloudtrace-v1-list-json>: at least one trace containing an ADK or ACME
# engine span (invoke_agent / call_llm / execute_tool / agent_run / acme.*), as opposed to
# plain Cloud Run request spans.
agent_traces_ok() {
  local n
  n="$(jq '[.traces[]? | select([.spans[]?.name] | any(test("^(invoke_agent|call_llm|execute_tool|agent_run|acme\\.)")))] | length' <<<"$1")"
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
    for gm in ${GEMINI_TEST_MODELS}; do
      code=\$(curl -s -o /tmp/vx.json -w '%{http_code}' -X POST -H \"Authorization: Bearer \$T\" \
        -H 'Content-Type: application/json' \
        '${base}/google/models/'\$gm':generateContent' \
        -d '{\"contents\":[{\"role\":\"user\",\"parts\":[{\"text\":\"Say ok\"}]}]}')
      echo \"gemini \$gm \$code\"; [ \"\$code\" = 200 ] || { head -c 300 /tmp/vx.json; echo; }
    done
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
  # Read-only query of the hub DB on the VM: both BUILD_PROJECT and EVAL_PROJECT exist with GITHUB_TOKEN secret.
  local out p
  out="$(vm_ssh "sudo sqlite3 -readonly /home/scion/.scion/hub.db \"
    SELECT 'project', p.slug FROM projects p WHERE p.slug IN ('${BUILD_PROJECT}','${EVAL_PROJECT}');
    SELECT 'secret', p.slug FROM secrets s JOIN projects p ON s.scope_id = p.id
      WHERE s.scope = 'project' AND s.key = 'GITHUB_TOKEN' AND p.slug IN ('${BUILD_PROJECT}','${EVAL_PROJECT}');\"" 2>/dev/null)"
  for p in "${BUILD_PROJECT}" "${EVAL_PROJECT}"; do
    grep -qx "project|${p}" <<<"${out}" || { fail hubproject "hub project ${p} not found"; return 1; }
    grep -qx "secret|${p}" <<<"${out}" || { fail hubproject "project secret GITHUB_TOKEN not set on ${p}"; return 1; }
  done
  pass "hubproject (${BUILD_PROJECT} + ${EVAL_PROJECT})"
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
  # Read-only: all 12 templates registered on the hub.
  local out t
  out="$(vm_ssh "sudo sqlite3 -readonly /home/scion/.scion/hub.db \"SELECT slug FROM templates;\"" 2>/dev/null)"
  for t in lead researcher product-manager backend frontend reviewer deployer eval-lead factory-analyst search-evaluator ui-craftsman eval-deployer; do
    grep -qx "${t}" <<<"${out}" || { fail team "template ${t} not registered on hub"; return 1; }
  done
  pass "team (12 templates across ${BUILD_PROJECT} and ${EVAL_PROJECT})"
}
check_topology() {
  # 2-project, 12-agent hierarchical topology:
  # - BUILD_PROJECT: lead at top -> 6 child workers (researcher, product-manager, backend, frontend, reviewer, deployer), message_mode=branch
  # - EVAL_PROJECT:  eval-lead at top -> 4 child workers (factory-analyst, search-evaluator, ui-craftsman, eval-deployer), message_mode=branch
  local rows r missing="" build_rows eval_rows b_msg e_msg bad_mode=""
  rows="$(printf '%s\n' ".separator '|'" \
    "SELECT p.slug, a.name, a.id, a.ancestry, a.message_mode FROM agents a JOIN projects p ON a.project_id = p.id WHERE a.deleted_at IS NULL;" | \
    gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" \
      --tunnel-through-iap --quiet --command="sudo sqlite3 -readonly /home/scion/.scion/hub.db" 2>/dev/null)"
  for r in lead researcher product-manager backend frontend reviewer deployer; do
    grep -q "^${BUILD_PROJECT}|${r}|" <<<"${rows}" || missing+=" ${BUILD_PROJECT}/${r}"
  done
  for r in eval-lead factory-analyst search-evaluator ui-craftsman eval-deployer; do
    grep -q "^${EVAL_PROJECT}|${r}|" <<<"${rows}" || missing+=" ${EVAL_PROJECT}/${r}"
  done
  [[ -z "${missing}" ]] || { fail topology "missing agents:${missing}"; return 1; }
  build_rows="$(awk -F'|' -v p="${BUILD_PROJECT}" '$1==p{print $2"|"$3"|"$4}' <<<"${rows}")"
  eval_rows="$(awk -F'|' -v p="${EVAL_PROJECT}" '$1==p{print $2"|"$3"|"$4}' <<<"${rows}")"
  b_msg="$(topology_ok "${build_rows}" 6 "lead")" || { fail topology "${BUILD_PROJECT}: ${b_msg}"; return 1; }
  e_msg="$(topology_ok "${eval_rows}" 4 "eval-lead")" || { fail topology "${EVAL_PROJECT}: ${e_msg}"; return 1; }
  bad_mode="$(awk -F'|' '$5 != "branch" && $2 !~ /^smoke-/ {print $1"/"$2"("$5")"}' <<<"${rows}" | tr '\n' ' ')"
  [[ -z "${bad_mode// /}" ]] || { fail topology "agents not in hierarchical branch message_mode: ${bad_mode}"; return 1; }
  pass "topology (${b_msg} in ${BUILD_PROJECT}; ${e_msg} in ${EVAL_PROJECT}; message_mode=branch)"
}
check_cost() {
  # Committed templates follow the model mapping, carry no artificial caps, and have app/model-specific eval prompts.
  local m c e
  m="$(models_ok "${REPO_ROOT}/docs/scion/templates")" || { fail cost "${m}"; return 1; }
  c="$(caps_ok "${REPO_ROOT}/docs/scion/templates")" || { fail cost "${c}"; return 1; }
  e="$(eval_prompts_ok "${REPO_ROOT}/docs/scion/templates")" || { fail cost "${e}"; return 1; }
  pass "cost (${m}; ${c}; ${e})"
}
check_skills() {
  # The hub-stored frontend and ui-craftsman templates carry a template-mounted impeccable skill.
  local out
  out="$(vm_ssh "sudo grep -rls '^name: impeccable' /home/scion/.scion --include=SKILL.md | grep -E '/(frontend|ui-craftsman)/' | head -2; \
                 sudo grep -rls 'gh://pbakaus/impeccable/' /home/scion/.scion --include=scion-agent.yaml | grep -E '/(frontend|ui-craftsman)/' | head -1 | sed 's/^/BADURI:/'" 2>/dev/null)"
  if grep -q '^BADURI:' <<<"${out}"; then fail skills "hub template still references a nested gh:// impeccable URI"; return 1; fi
  if grep -q 'SKILL.md' <<<"${out}"; then pass skills; else fail skills "impeccable SKILL.md not mounted in hub-stored frontend/ui-craftsman templates"; fi
}
check_loop() {
  # Both 15-minute heartbeat schedules exist, are active, and target lead and eval-lead.
  local s row cron status payload
  for s in "${BUILD_PROJECT}-lead-heartbeat:lead" "${EVAL_PROJECT}-lead-heartbeat:eval-lead"; do
    local sname="${s%%:*}" target="${s#*:}"
    row="$(printf '%s\n' ".separator '|'" "SELECT cron_expr, status, payload FROM schedules WHERE name='${sname}';" | \
      gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet \
        --command="sudo sqlite3 -readonly /home/scion/.scion/hub.db" 2>/dev/null)"
    [[ -n "${row}" ]] || { fail loop "schedule ${sname} not found"; return 1; }
    IFS='|' read -r cron status payload <<<"${row}"
    [[ "${status}" == "active" ]] || { fail loop "schedule ${sname} status=${status}"; return 1; }
    [[ "${payload}" == *"${target}"* ]] || { fail loop "schedule ${sname} does not target ${target}"; return 1; }
    cron_interval_ok "${cron}" 15 || { fail loop "cron '${cron}' fires more often than every 15 min"; return 1; }
  done
  pass "loop (${BUILD_PROJECT}-lead-heartbeat & ${EVAL_PROJECT}-lead-heartbeat, ${HEARTBEAT_CRON}, active)"
}
check_budget() {
  # Budget exists with a 100% threshold wired to the guard topic, and the guard service is deployed.
  local b
  b="$(gcloud billing budgets list --billing-account="${BILLING_ACCOUNT}" \
        --filter="displayName=${BUDGET_NAME}" --format=json 2>/dev/null)"
  [[ "$(jq 'length' <<<"${b:-[]}")" -ge 1 ]] || { fail budget "budget ${BUDGET_NAME} not found"; return 1; }
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
check_webaccess() {
  # 1. New web users get the least privilege by default (viewer).
  # 2. Every domain in WEB_DOMAINS is in Hub settings.yaml authorized_domains.
  # 3. Every domain in WEB_DOMAINS has roles/iap.httpsResourceAccessor on all deployed IAP Cloud Run services.
  local auth_block role d s pol missing=""
  auth_block="$(vm_ssh "sudo sed -n '/^  auth:/,/^  [a-z_]*:\$/p' /home/scion/.scion/settings.yaml" 2>/dev/null)"
  role="$(awk '/default_user_role:/ {print $2}' <<<"${auth_block}" | tail -1)"
  [[ "${role}" == "viewer" ]] || missing+=" default_user_role:${role:-unset}(want:viewer)"
  local -a doms
  read -ra doms <<<"${WEB_DOMAINS}"
  for d in "${doms[@]}"; do
    grep -q "\"${d}\"" <<<"${auth_block}" || missing+=" hub-authorized_domains:${d}"
  done
  for s in "${PROXY_SVC}" "${FACTORY_PREFIX}-observatory" "${FACTORY_PREFIX}-irrops-demo"; do
    if gcloud run services describe "${s}" --region="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
      pol="$(gcloud iap web get-iam-policy --project="${PROJECT_ID}" --resource-type=cloud-run --service="${s}" --region="${REGION}" --format=json 2>/dev/null)"
      for d in "${doms[@]}"; do
        if ! jq -e --arg m "domain:${d}" '.bindings[]? | select(.role=="roles/iap.httpsResourceAccessor") | .members | index($m)' <<<"${pol}" >/dev/null; then
          # Secondary Google Workspace domains are canonicalized by GCP IAM to the customer's primary domain;
          # verify that binding domain:${d} succeeds as a no-op and leaves the canonicalized bindings unchanged.
          local after_pol
          if ! gcloud iap web add-iam-policy-binding --project="${PROJECT_ID}" \
                 --resource-type=cloud-run --service="${s}" --region="${REGION}" \
                 --member="domain:${d}" --role=roles/iap.httpsResourceAccessor --condition=None --format=none >/dev/null 2>&1 \
             || ! after_pol="$(gcloud iap web get-iam-policy --project="${PROJECT_ID}" --resource-type=cloud-run --service="${s}" --region="${REGION}" --format=json 2>/dev/null)" \
             || [[ "$(jq -cS '.bindings' <<<"${after_pol}")" != "$(jq -cS '.bindings' <<<"${pol}")" ]]; then
            missing+=" iap(${s}):domain:${d}"
          fi
        fi
      done
    fi
  done
  [[ -z "${missing}" ]] || { fail webaccess "${missing# }"; return 1; }
  pass "webaccess (viewer default; domains: ${WEB_DOMAINS})"
}
check_backups() {
  # The hub VM boot disk has a daily snapshot schedule attached.
  local pol
  pol="$(gcloud compute disks describe "${VM_NAME}" --zone="$(resolve_zone)" --project="${PROJECT_ID}" \
          --format='value(resourcePolicies)' 2>/dev/null)"
  [[ "${pol}" == *"${VM_NAME}-daily-snapshots"* ]] || { fail backups "no snapshot schedule on disk ${VM_NAME}"; return 1; }
  pass backups
}
check_watchdog() {
  # Wake-on-change watchdog + daily lead reincarnation + PO log-based alert policy (infra/76-watchdog.sh).
  local p="${PROJECT_ID}" r="${REGION}" svc="acme-factory-watchdog" missing=""
  gcloud iam service-accounts describe "${svc}@${p}.iam.gserviceaccount.com" --project="${p}" >/dev/null 2>&1 || missing+=" sa:${svc}"
  gcloud secrets versions list acme-watchdog-hub-token --project="${p}" --filter=state=ENABLED --format='value(name)' 2>/dev/null | grep -q . \
    || missing+=" secret:acme-watchdog-hub-token"
  gcloud run services describe "${svc}" --region="${r}" --project="${p}" >/dev/null 2>&1 || missing+=" run:${svc}"
  for j in acme-factory-watchdog acme-lead-reincarnate; do
    local st
    st="$(gcloud scheduler jobs describe "${j}" --location="${r}" --project="${p}" --format='value(state)' 2>/dev/null || true)"
    [[ "${st}" == "ENABLED" ]] || missing+=" scheduler:${j}(${st:-missing})"
  done
  local wd_sched
  wd_sched="$(gcloud scheduler jobs describe acme-factory-watchdog --location="${r}" --project="${p}" --format='value(schedule)' 2>/dev/null || true)"
  [[ "${wd_sched}" == "${WATCHDOG_CRON:-0 * * * *}" ]] || missing+=" watchdog-cron:${wd_sched:-unset}(want:${WATCHDOG_CRON:-0 * * * *})"
  gcloud alpha monitoring policies list --project="${p}" --filter='displayName="acme-factory-watchdog-po-alert" AND enabled=true' \
    --format='value(name)' 2>/dev/null | grep -q . || missing+=" alert:acme-factory-watchdog-po-alert"
  [[ -z "${missing}" ]] || { fail watchdog "missing:${missing}"; return 1; }
  if gcloud run services get-iam-policy "${svc}" --region="${r}" --project="${p}" --format=json 2>/dev/null \
       | jq -e '[.bindings[]?.members[]?] | any(. == "allUsers" or . == "allAuthenticatedUsers")' >/dev/null; then
    fail watchdog "${svc} is publicly invokable"; return 1
  fi
  pass "watchdog (hourly wake-on-change, 04:00Z daily reincarnate, PO alert policy)"
}
check_identity() {
  # Dedicated low-privilege agent SA (assign mode), hub default block, least-privilege IAM (infra/25-agent-identity.sh).
  local p="${PROJECT_ID}" agent_sa="acme-agent@${PROJECT_ID}.iam.gserviceaccount.com"
  local vm_sa="${VM_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  local dep_sa="acme-observatory-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
  local missing="" pol
  gcloud iam service-accounts describe "${agent_sa}" --project="${p}" >/dev/null 2>&1 || missing+=" sa:acme-agent"
  pol="$(gcloud projects get-iam-policy "${p}" --format=json 2>/dev/null)"
  _has() { jq -e --arg r "$1" --arg m "$2" '.bindings[] | select(.role==$r) | .members | index($m)' <<<"${pol}" >/dev/null; }
  local role
  for role in roles/aiplatform.user roles/logging.logWriter roles/monitoring.metricWriter roles/cloudtrace.agent; do
    _has "${role}" "serviceAccount:${agent_sa}" || missing+=" ${role}->acme-agent"
  done
  if _has "roles/iam.serviceAccountAdmin" "serviceAccount:${vm_sa}"; then
    missing+=" forbidden:serviceAccountAdmin-on-${VM_NAME}"
  fi
  if _has "roles/run.developer" "serviceAccount:${dep_sa}"; then
    missing+=" forbidden:project-run.developer-on-deployer"
  fi
  if _has "roles/iap.httpsResourceAccessor" "user:${ADMIN_EMAIL}"; then
    missing+=" forbidden:project-iap-on-${ADMIN_EMAIL}"
  fi
  gcloud iam service-accounts get-iam-policy "${agent_sa}" --project="${p}" --format=json 2>/dev/null \
    | jq -e --arg m "serviceAccount:${vm_sa}" '.bindings[]? | select(.role=="roles/iam.serviceAccountTokenCreator") | .members | index($m)' >/dev/null \
    || missing+=" tokenCreator(${VM_NAME})->acme-agent"
  local hub_state
  hub_state="$(vm_ssh "sudo grep -E '^default_gcp_identity_mode:' /home/scion/.scion/settings.yaml | awk '{print \$2}' | tail -1; \
    sudo -u scion sqlite3 /home/scion/.scion/hub.db \"SELECT json_extract(annotations, '\\\$.\\\"scion.io/default-gcp-identity-mode\\\"') FROM projects WHERE slug='${HUB_PROJECT_SLUG}';\"; \
    sudo -u scion sqlite3 /home/scion/.scion/hub.db \"SELECT COUNT(*) FROM agents WHERE deleted_at IS NULL AND COALESCE(json_extract(applied_config, '\\\$.gcpIdentity.metadataMode'),'') != 'assign';\"" 2>/dev/null)"
  local hub_mode proj_mode non_assign
  hub_mode="$(sed -n '1p' <<<"${hub_state}")"
  proj_mode="$(sed -n '2p' <<<"${hub_state}")"
  non_assign="$(sed -n '3p' <<<"${hub_state}")"
  [[ "${hub_mode}" == "block" ]] || missing+=" hub-mode:${hub_mode:-unset}(want:block)"
  [[ "${proj_mode}" == "assign" ]] || missing+=" project-mode:${proj_mode:-unset}(want:assign)"
  [[ "${non_assign}" == "0" ]] || missing+=" non-assign-agents:${non_assign}"
  [[ -z "${missing}" ]] || { fail identity "${missing}"; return 1; }
  pass "identity (acme-agent@ assign mode, hub default block, least-privilege IAM)"
}
check_telemetry() {
  # Scion agent telemetry export enabled in hub settings (infra/25-agent-identity.sh).
  local tel
  tel="$(vm_ssh "sudo -u scion sqlite3 /home/scion/.scion/hub.db \"SELECT json_extract(value, '\\\$.enabled'), json_extract(value, '\\\$.cloud.enabled') FROM hub_settings WHERE section='telemetry';\"" 2>/dev/null)"
  [[ "${tel}" == "1|1" ]] || { fail telemetry "hub telemetry not enabled (${tel:-unset})"; return 1; }
  pass "telemetry (Cloud Monitoring OTLP export enabled)"
}
check_lead() {
  # Both lead (BUILD_PROJECT) and eval-lead (EVAL_PROJECT) containers are running on the VM.
  local names
  names="$(vm_ssh "sudo docker ps --format '{{.Names}}'" 2>/dev/null)"
  grep -q 'lead' <<<"${names}" || { fail lead "no running container for lead"; return 1; }
  grep -q 'eval-lead' <<<"${names}" || { fail lead "no running container for eval-lead"; return 1; }
  pass "lead (lead & eval-lead running)"
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
    webaccess)    check_webaccess ;;
    backups)      check_backups ;;
    watchdog)     check_watchdog ;;
    identity)     check_identity ;;
    telemetry)    check_telemetry ;;
    agent-engine) check_agent_engine ;;
    engines)      check_engines ;;
    traces)       check_traces ;;
    demo-engine)  check_demo_engine ;;
    all)
      local rc=0
      for c in project hub vertex claude-image hubproject smoke team lead topology cost skills loop budget observatory iap-oauth agent-engine webaccess backups watchdog identity telemetry; do run "$c" || rc=1; done
      return "$rc" ;;
    e2e)
      local rc=0
      for c in lead loop engines traces demo-engine; do run "$c" || rc=1; done
      return "$rc" ;;
    *) echo "usage: $0 <project|hub|vertex|claude-image|hubproject|smoke|team|lead|topology|cost|skills|loop|budget|observatory|iap-oauth|irrops-demo|webaccess|backups|watchdog|identity|telemetry|agent-engine|engines|traces|demo-engine|e2e|all>" >&2; return 2 ;;
  esac
}

[[ -n "${VERIFY_LIB_ONLY:-}" ]] || run "${1:-all}"
