#!/usr/bin/env bash
# Bootstrap a Scion software factory end to end (see docs/scion/02-runbook-from-zero.md).
#
# Runs every infra step in order, runs the matching infra/verify.sh check after each,
# and pauses at the manual (console) steps. Every step is idempotent, so re-running
# resumes safely. Steps already passing are skipped unless --force.
#
# Usage:
#   docs/scion/scripts/bootstrap.sh              # all steps
#   docs/scion/scripts/bootstrap.sh --from 7     # resume from step 7
#   docs/scion/scripts/bootstrap.sh --only 10    # one step
#   docs/scion/scripts/bootstrap.sh --list       # show the plan
#   docs/scion/scripts/bootstrap.sh --force      # re-run steps even if their check passes
#
# Prereqs: infra/factory.env and infra/deploy-config.json edited for the new factory;
# gcloud (Owner on folder/project, Billing User), gh (repo admin), jq, uv, git.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "${REPO_ROOT}"
INFRA="${REPO_ROOT}/infra"
# shellcheck source=infra/lib.sh
source "${INFRA}/lib.sh"

FROM=0; ONLY=""; FORCE=0; LIST=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --list) LIST=1; shift ;;
    *) sed -n '2,16p' "$0"; exit 2 ;;
  esac
done

say()   { printf '\n\033[1m[%s] %s\033[0m\n' "$1" "$2"; }
check() { bash "${INFRA}/verify.sh" "$@"; }
pause() {  # pause <what to do>
  printf '\n🖐  MANUAL STEP\n%s\n' "$1"
  if [[ -t 0 ]]; then read -rp "Press Enter when done (Ctrl-C to stop)… " _; else echo "(non-interactive: stopping here)"; exit 3; fi
}
admin() { bash "${INFRA}/30-hub-admin-access.sh" "$1"; }

push_templates() { bash "${INFRA}/45-push-templates.sh"; }  # opens/closes the admin window itself

# step | title | check(s) | function
STEPS=(
  "1|GCP project, billing, APIs, default VPC|project|step1"
  "2|Hub VM + IAP proxy (Scion deploy.sh)|hub|step2"
  "3|Vertex access + Claude image (+ Model Garden)|vertex claude-image|step3"
  "4|Custom IAP OAuth client (external domains)|iap-oauth|step4"
  "5|Domain web access (IAP + hub sign-in policy)|webaccess|step5"
  "6|Hub project + GITHUB_TOKEN secret|hubproject|step6"
  "7|Agent team templates (build + push)|team cost skills|step7"
  "8|Start the lead (it forms the standing roster)|lead|step8"
  "9|Loop schedule (TICK)|loop|step9"
  "10|Budget guard (suspends agents at 100%)|budget|step10"
  "11|Observatory platform (optional)|observatory|step11"
  "12|Daily boot-disk snapshots|backups|step12"
)

step1()  { bash "${INFRA}/00-bootstrap-project.sh"; }
step2()  { bash "${INFRA}/10-deploy-hub.sh"; }
step3()  { bash "${INFRA}/20-model-access.sh"
           pause "Vertex AI → Model Garden (project ${PROJECT_ID}): enable the Claude models the templates use
(Opus, Sonnet and Haiku at the versions in factory/templates/*/scion-agent.yaml and ANTHROPIC_DEFAULT_HAIKU_MODEL)."; }
step4()  { pause "Google Auth Platform (project ${PROJECT_ID}):
  1. Branding: app name and support email (${ADMIN_EMAIL})
  2. Audience: External, then Publish app (In production)
  3. Clients: Web application; redirect URI https://iap.googleapis.com/v1/oauth/clientIds/<CLIENT_ID>:handleRedirect
Next, the script prompts for the client ID and (hidden) secret."
           bash "${INFRA}/55-iap-oauth-client.sh"; }
step5()  { bash "${INFRA}/50-web-access.sh"; }
step6()  { admin enable; trap 'admin disable' RETURN
           hub_cli hub projects create "https://github.com/${GITHUB_REPO}.git" || true
           local pid; pid="$(hub_cli hub projects --json 2>/dev/null | jq -r --arg r "${GITHUB_REPO}" '.[] | select(.gitRemote=="github.com/"+$r) | .id')"
           [[ -n "${pid}" ]] || { echo "hub project for ${GITHUB_REPO} not found" >&2; return 1; }
           pause "Create a GitHub fine-grained PAT on ${GITHUB_REPO}: Contents, Pull requests, Issues and Workflows (read/write)."
           local pat; read -rsp "Paste the PAT (hidden): " pat; echo
           printf '%s' "${pat}" | bash "${INFRA}/31-set-project-secret.sh" "${pid}" GITHUB_TOKEN; unset pat; }
step7()  { bash "${INFRA}/40-build-team-templates.sh"; push_templates; }
step8()  { admin enable; trap 'admin disable' RETURN
           hub_cli --project "${HUB_PROJECT_SLUG}" start acme-lead --type acme-lead --role full --no-wait \
             "You are acme-lead. Read your instructions and the product brief. Form the standing roster (your instructions, section Standing roster). Create the pinned issue titled exactly: 🏭 Factory status. Plan the MVP as GitHub issues, then start the factory loop."; }
step9()  { bash "${INFRA}/60-factory.sh" tick-install; }
step10() { bash "${INFRA}/70-budget-guard.sh"
           gcloud pubsub topics publish acme-budget-alerts --project="${PROJECT_ID}" \
             --message='{"costAmount":1.23,"budgetAmount":1000.0}' --format=none
           echo "Wiring test published; the guard log should show: cost=1.23 budget=1000.0 -> no action (HTTP 204)"; }
step11() { bash "${INFRA}/80-observatory-platform.sh"
           pause "Billing → Billing export → Standard usage cost → project ${PROJECT_ID}, dataset billing_export.
Then store a GitHub token for the collector:
  read -rs T && printf %s \"\$T\" | gcloud secrets versions add acme-observatory-github-token --project=${PROJECT_ID} --data-file=- && unset T"; }
step12() { bash "${INFRA}/75-backups.sh"; }

for s in "${STEPS[@]}"; do
  IFS='|' read -r n title checks fn <<<"${s}"
  if (( LIST )); then printf '%3s  %-52s check: %s\n' "${n}" "${title}" "${checks:--}"; continue; fi
  [[ -n "${ONLY}" && "${n}" != "${ONLY}" ]] && continue
  (( n < FROM )) && continue
  say "${n}" "${title}"
  if [[ -n "${checks}" && "${FORCE}" == 0 ]]; then
    read -ra cs <<<"${checks}"; ok=1
    for c in "${cs[@]}"; do check "${c}" >/dev/null 2>&1 || ok=0; done
    if (( ok )); then echo "already passing (${checks}); skipping (use --force to re-run)"; continue; fi
  fi
  "${fn}"
  if [[ -n "${checks}" ]]; then read -ra cs <<<"${checks}"; for c in "${cs[@]}"; do check "${c}"; done; fi
done
(( LIST )) && exit 0
say "✓" "Done. Final gate: infra/verify.sh all"
check all
[[ "$(admin status 2>/dev/null | tail -1)" == "disabled" ]] || echo "WARNING: admin access is enabled; run: infra/30-hub-admin-access.sh disable"
