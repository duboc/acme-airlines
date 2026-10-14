#!/usr/bin/env bash
# Exercises deploy-collector.sh and verify-deploy.sh with a fake gcloud on PATH. No GCP needed.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
UUID=61b6ae3d-9335-4360-a259-5b306dc229f6
export IMG=repo/collector:abc123 REGION=r PROJECT=p AR_REPO=repo GITHUB_SHA=abc123 CALLS="$work/calls" HUB_PROJECT=$UUID

# Fake gcloud, driven by env: PROBE=denied|empty|enabled, FIRST=ok|notfound|other, JOB_JSON=file.
cat >"$work/gcloud" <<'F'
#!/usr/bin/env bash
echo "$*" >>"$CALLS"
case "$*" in
  "secrets versions list"*)
    case "$PROBE" in
      denied) echo "PERMISSION_DENIED" >&2; exit 1 ;;
      empty) exit 0 ;;
      enabled) echo "versions/1"; exit 0 ;;
    esac ;;
  *"--update-secrets"*)
    case "$FIRST" in
      ok) exit 0 ;;
      notfound) echo "ERROR: Secret projects/1/secrets/acme-observatory-github-token/versions/latest was not found" >&2; exit 1 ;;
      other) echo "ERROR: quota exceeded" >&2; exit 1 ;;
      denied) echo "ERROR: Permission denied on secret projects/1/secrets/acme-observatory-github-token/versions/latest" >&2; exit 1 ;;
      image) echo "ERROR: Image 'repo/collector:abc123' not found" >&2; exit 1 ;;
      othersecret) echo "ERROR: Secret projects/1/secrets/some-other-secret/versions/latest was not found" >&2; exit 1 ;;
    esac ;;
  *"--remove-secrets=GITHUB_TOKEN"*) exit 0 ;;
  "run services describe"*) echo "$IMG_DASH" ;;
  "run jobs describe"*) cat "$JOB_JSON" ;;
esac
F
chmod +x "$work/gcloud"
export PATH="$work:$PATH"

fails=0
check() { # name expected_rc actual_rc
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1 (rc $3, want $2)"; fails=$((fails + 1)); fi
}
has() { grep -q -- "$2" "$CALLS" && echo "ok   $1" || { echo "FAIL $1: no call matching $2"; fails=$((fails + 1)); }; }
hasnt() { grep -q -- "$2" "$CALLS" && { echo "FAIL $1: unexpected $2"; fails=$((fails + 1)); } || echo "ok   $1"; }
run() { : >"$CALLS"; "$here/deploy-collector.sh" >"$work/out" 2>&1; echo $?; }

PROBE=enabled FIRST=ok; export PROBE FIRST
check "enabled version -> maps secret" 0 "$(run)"; has "uses update-secrets" "update-secrets=GITHUB_TOKEN"
has "sets HUB_PROJECT to the UUID" "update-env-vars=HUB_PROJECT=$UUID"
hasnt "never replaces all env vars" "set-env-vars"

PROBE=empty; export PROBE
check "empty version list -> remove" 0 "$(run)"; has "uses remove-secrets" "remove-secrets=GITHUB_TOKEN"
has "HUB_PROJECT also set when the token is skipped" "update-env-vars=HUB_PROJECT=$UUID"
hasnt "no update attempt" "update-secrets"; grep -q '::warning::' "$work/out" && echo "ok   warning" || { echo "FAIL warning"; fails=$((fails+1)); }

PROBE=denied FIRST=ok; export PROBE FIRST
check "denied probe, deploy ok" 0 "$(run)"; hasnt "no remove" "remove-secrets"

FIRST=notfound; export FIRST
check "denied probe, secret not found -> remove" 0 "$(run)"; has "falls back" "remove-secrets=GITHUB_TOKEN"

FIRST=other; export FIRST
check "other failure is not masked" 1 "$(run)"; hasnt "no fallback" "remove-secrets"
for f in denied image othersecret; do
  FIRST=$f; export FIRST
  check "$f error does not trigger fallback" 1 "$(run)"; hasnt "$f: no remove-secrets" "remove-secrets"
done


# HUB_PROJECT is required: an unset value must fail the deploy, not deploy a wrong job.
(unset HUB_PROJECT; PROBE=enabled FIRST=ok; : >"$CALLS"; "$here/deploy-collector.sh" >/dev/null 2>&1; check "unset HUB_PROJECT fails the deploy" 1 $?)

# Static guard: the replace-all secret flags must never be used (comments excluded).
if grep -v '^[[:space:]]*#' "$here"/*.sh ../../.github/workflows/observatory-deploy.yml 2>/dev/null \
  | grep -v 'test_deploy.sh' | grep -q -E -- '--(set|clear)-secrets'; then
  echo "FAIL forbidden --set-secrets/--clear-secrets present"; fails=$((fails + 1))
else echo "ok   no --set-secrets/--clear-secrets"; fi

# verify-deploy.sh
export IMG_DASH="repo/dashboard:abc123"
cat >"$work/good.json" <<J
{"spec":{"template":{"spec":{"template":{"spec":{"containers":[{"image":"repo/collector:abc123","env":[{"name":"HUB_PROJECT","value":"61b6ae3d-9335-4360-a259-5b306dc229f6"},{"name":"SCION_HUB_TOKEN","valueFrom":{"secretKeyRef":{"name":"x","key":"latest"}}}]}]}}}}}}
J
sed 's/SCION_HUB_TOKEN/OTHER/' "$work/good.json" >"$work/nohub.json"
sed 's/collector:abc123/collector:old/' "$work/good.json" >"$work/oldimg.json"
sed 's/"value":"61b6ae3d[^"]*"/"value":"duboc-acme-airlines"/' "$work/good.json" >"$work/slug.json"
sed 's/"name":"HUB_PROJECT"/"name":"OTHER_VAR"/' "$work/good.json" >"$work/nohubproject.json"
JOB_JSON=$work/good.json "$here/verify-deploy.sh" >/dev/null 2>&1; check "verify ok" 0 $?
JOB_JSON=$work/nohub.json "$here/verify-deploy.sh" >/dev/null 2>&1; check "verify fails without SCION_HUB_TOKEN" 1 $?
JOB_JSON=$work/slug.json "$here/verify-deploy.sh" >/dev/null 2>&1; check "verify fails when HUB_PROJECT is the slug" 1 $?
JOB_JSON=$work/nohubproject.json "$here/verify-deploy.sh" >/dev/null 2>&1; check "verify fails without HUB_PROJECT" 1 $?
JOB_JSON=$work/oldimg.json "$here/verify-deploy.sh" >/dev/null 2>&1; check "verify fails on old collector image" 1 $?
IMG_DASH=repo/dashboard:old JOB_JSON=$work/good.json "$here/verify-deploy.sh" >/dev/null 2>&1; check "verify fails on old dashboard image" 1 $?

[ "$fails" = 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
