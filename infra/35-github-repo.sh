#!/usr/bin/env bash
# GitHub repository configuration for the Scion software factory (matching duboc/scion-experiment). Idempotent.
#
# Creates/updates:
#   1. Project labels (${BUILD_PROJECT}, ${EVAL_PROJECT}), wave labels (wave-1..wave-5), and workflow labels.
#   2. Pinned "🏭 Factory status" issue (created if missing).
#   3. Collaborators-only interaction limit on public repositories.
#   4. Independent orphan trunk branch (${EVAL_BRANCH}) initialized from observatory/ if missing on origin.
#   5. Branch protection on `main` (required status checks: backend, engine-smoke, frontend, pr-size;
#      enforce_admins=false so commit-slot pushes from the build factory succeed while force-pushes/deletions are blocked).
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
ensure_label "${BUILD_PROJECT}"   "0e8a16" "build factory backlog (${BUILD_PROJECT})"
ensure_label "${EVAL_PROJECT}"    "1d76db" "eval factory backlog (${EVAL_PROJECT})"
for n in 1 2 3 4 5; do
  ensure_label "wave-${n}"        "bfd4f2" "Execution wave ${n}"
done

ensure_label "status:triage"      "fbca04" "Needs lead triage and acceptance criteria"
ensure_label "status:spec-ready"  "5319e7" "Research brief complete; ready for product-manager user story"
ensure_label "status:ready"       "0e8a16" "Triaged and ready for agent dispatch"
ensure_label "status:in-progress" "1d76db" "Assigned to an active worker agent"
ensure_label "status:in-review"   "5319e7" "Awaiting eval-lead independent verification and approval"
ensure_label "status:done"        "0e8a16" "Verified, approved, and closed by eval-lead"
ensure_label "status:blocked"     "d93f0b" "Blocked on dependency or operator action"
ensure_label "status:proposed"    "c5def5" "Proposed in idle mode; awaits PO approval"
ensure_label "priority:p0"        "b60205" "Critical: broken main, deploy, or security blocker"
ensure_label "priority:p1"        "d93f0b" "High-priority backlog item"
ensure_label "priority:p2"        "0e8a16" "Normal backlog item"
ensure_label "size:exception"     "e99695" "Approved exception to the 400-line PR size gate"

for r in researcher product-manager backend frontend reviewer deployer factory-analyst search-evaluator ui-craftsman eval-deployer; do
  ensure_label "role:${r}" "bfd4f2" "Target specialist role: ${r}"
done

log "2. Pinned '🏭 Factory status' issue"
STATUS_NUM="$(gh issue list --repo "${REPO}" --state open --search '"🏭 Factory status" in:title' --json number --jq '.[0].number // empty')"
if [[ -z "${STATUS_NUM}" ]]; then
  URL="$(gh issue create --repo "${REPO}" --title "🏭 Factory status" \
    --body "Factory status board. Updated by \`lead\` and \`eval-lead\`.")"
  STATUS_NUM="${URL##*/}"
  gh issue pin "${STATUS_NUM}" --repo "${REPO}" 2>/dev/null || true
  echo "Created and pinned Factory status issue #${STATUS_NUM}"
else
  echo "Factory status issue #${STATUS_NUM} already exists"
fi

log "3. Ensure open backlog issues carry project (${BUILD_PROJECT} / ${EVAL_PROJECT}) and wave labels"
gh issue list --repo "${REPO}" --state open --limit 50 --json number,title,labels \
  | python3 -c '
import json, subprocess, sys
repo, build_proj, eval_proj = sys.argv[1], sys.argv[2], sys.argv[3]
issues = json.load(sys.stdin)
for it in issues:
    num = it["number"]
    title = it["title"]
    if "Factory status" in title:
        continue
    labels = {l["name"] for l in it.get("labels", [])}
    add = []
    if build_proj not in labels and eval_proj not in labels:
        if "Observatory" in title or "area:observatory" in labels:
            add.append(eval_proj)
        else:
            add.append(build_proj)
    if not any(l.startswith("wave-") for l in labels):
        add.append("wave-1")
    if add:
        subprocess.run(["gh", "issue", "edit", str(num), "--repo", repo, "--add-label", ",".join(add)], check=False)
' "${REPO}" "${BUILD_PROJECT}" "${EVAL_PROJECT}"

log "4. Collaborators-only interaction limit (if repo is public)"
VIS="$(gh api "repos/${REPO}" --jq '.visibility' 2>/dev/null || echo 'private')"
if [[ "${VIS}" == "public" ]]; then
  gh api -X PUT "repos/${REPO}/interaction-limits" \
    -H "Accept: application/vnd.github+json" \
    -f limit="collaborators_only" \
    -f expiry="six_months" >/dev/null
  echo "Interaction limit set to collaborators_only (six_months)"
fi

log "5. Ensure independent orphan branch '${EVAL_BRANCH}' exists on origin"
if ! git ls-remote --exit-code --heads "https://github.com/${REPO}.git" "${EVAL_BRANCH}" >/dev/null 2>&1; then
  TMP_ORPHAN="$(mktemp -d)"
  (
    cd "${TMP_ORPHAN}"
    git init -q
    git checkout --orphan "${EVAL_BRANCH}"
    cp -R "${REPO_ROOT}/observatory" "${TMP_ORPHAN}/observatory"
    cp "${REPO_ROOT}/DESIGN.md" "${REPO_ROOT}/PRODUCT.md" "${REPO_ROOT}/.gitignore" "${TMP_ORPHAN}/" 2>/dev/null || true
    git add -A
    git commit -q -m "chore(eval): initialize independent ${EVAL_BRANCH} orphan trunk"
    git remote add origin "git@github.com:${REPO}.git"
    git push -u origin "${EVAL_BRANCH}"
  )
  rm -rf "${TMP_ORPHAN}"
  echo "Created and pushed orphan branch ${EVAL_BRANCH}"
else
  echo "Orphan branch ${EVAL_BRANCH} already exists on origin"
fi

log "6. Branch protection on main"
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
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
echo "Branch protection configured on ${REPO}:main (checks: ${CHECKS}, enforce_admins: false)"
