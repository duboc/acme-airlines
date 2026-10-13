#!/usr/bin/env bash
# GitHub repository configuration for the Scion software factory. Idempotent.
#
# Creates/updates:
#   1. Workflow, priority, role, and governance labels (incl. status:proposed, size:exception).
#   2. Pinned "🏭 Factory status" issue (created if missing).
#   3. Collaborators-only interaction limit on public repositories.
#   4. Branch protection on `main` (PR required, enforce_admins=true, required status checks:
#      backend, engine-smoke, frontend, pr-size).
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"

REPO="${GITHUB_REPO}"
log() { printf '\n==> %s\n' "$*"; }

ensure_label() {
  local name="$1" color="$2" desc="$3"
  gh label create "${name}" --repo "${REPO}" --color "${color}" --description "${desc}" --force >/dev/null
}

log "1. GitHub labels (${REPO})"
ensure_label "status:triage"      "fbca04" "Needs lead triage and acceptance criteria"
ensure_label "status:ready"       "0e8a16" "Triaged and ready for agent dispatch"
ensure_label "status:in-progress" "1d76db" "Assigned to an active worker agent"
ensure_label "status:in-review"   "5319e7" "PR open and awaiting acme-reviewer verdict"
ensure_label "status:blocked"     "d93f0b" "Blocked on dependency or operator action"
ensure_label "status:proposed"    "c5def5" "Proposed by acme-lead in idle mode; awaits PO approval"
ensure_label "priority:p0"        "b60205" "Critical: broken main, deploy, or security blocker"
ensure_label "priority:p1"        "d93f0b" "High-priority backlog item"
ensure_label "priority:p2"        "0e8a16" "Normal backlog item"
ensure_label "size:exception"     "e99695" "Approved exception to the 400-line PR size gate"

for r in architect backend frontend qa qa-tester reviewer security-auditor investigator researcher doc-writer release-notes; do
  ensure_label "role:${r}" "bfd4f2" "Target specialist role: acme-${r}"
done

log "2. Pinned '🏭 Factory status' issue"
STATUS_NUM="$(gh issue list --repo "${REPO}" --state open --search '"🏭 Factory status" in:title' --json number --jq '.[0].number // empty')"
if [[ -z "${STATUS_NUM}" ]]; then
  URL="$(gh issue create --repo "${REPO}" --title "🏭 Factory status" \
    --body "Factory status board. Updated by \`acme-lead\` on every loop pass.")"
  STATUS_NUM="${URL##*/}"
  gh issue pin "${STATUS_NUM}" --repo "${REPO}" 2>/dev/null || true
  echo "Created and pinned Factory status issue #${STATUS_NUM}"
else
  echo "Factory status issue #${STATUS_NUM} already exists"
fi

log "3. Collaborators-only interaction limit (if repo is public)"
VIS="$(gh api "repos/${REPO}" --jq '.visibility' 2>/dev/null || echo 'private')"
if [[ "${VIS}" == "public" ]]; then
  gh api -X PUT "repos/${REPO}/interaction-limits" \
    -H "Accept: application/vnd.github+json" \
    -f limit="collaborators_only" \
    -f expiry="six_months" >/dev/null
  echo "Interaction limit set to collaborators_only (six_months)"
fi

log "4. Branch protection on main"
# Include pr-size once the workflow is on main, or when FORCE_PR_SIZE_CHECK=1
CHECKS='["backend","engine-smoke","frontend"]'
if [[ "${FORCE_PR_SIZE_CHECK:-0}" == "1" ]] || gh api "repos/${REPO}/contents/.github/workflows/pr-size.yml" >/dev/null 2>&1; then
  CHECKS='["backend","engine-smoke","frontend","pr-size"]'
fi

gh api -X PUT "repos/${REPO}/branches/main/protection" \
  -H "Accept: application/vnd.github+json" \
  --input - >/dev/null <<EOF
{
  "required_status_checks": {
    "strict": false,
    "contexts": ${CHECKS}
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
echo "Branch protection configured on ${REPO}:main (checks: ${CHECKS})"
