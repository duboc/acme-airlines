#!/usr/bin/env bash
# Static checks for infra/90-irrops-demo-platform.sh (no cloud access needed).
# Usage: infra/tests/test_irrops_demo_script.sh
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
S=infra/90-irrops-demo-platform.sh
fails=0
check() {  # check <name> <command...>
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "ok   ${name}"; else echo "FAIL ${name}"; fails=$((fails+1)); fi
}
has() { grep -qE -- "$1" "${S}"; }
lacks() { ! grep -qE -- "$1" "${S}"; }

check "bash -n"                              bash -n "${S}"
check "bash -n verify.sh and 55"             bash -c 'bash -n infra/verify.sh && bash -n infra/55-iap-oauth-client.sh'
if command -v shellcheck >/dev/null; then
  check "shellcheck"                         shellcheck -x -s bash "${S}" infra/verify.sh
else echo "skip shellcheck (not installed)"; fi
check "strict mode header"                   has '^set -euo pipefail$'
check "executable"                           test -x "${S}"
check "not public (no-allow-unauthenticated)" has -- '--no-allow-unauthenticated'
check "IAP enabled"                          has -- '--iap( |$)'
check "single instance"                      has -- '--max-instances=1'
check "fake LLM"                             has 'LLM_MODE=fake'
check "never allUsers"                       lacks 'allUsers|allAuthenticatedUsers|--allow-unauthenticated'
check "no project-level binding"             lacks 'add-iam-policy-binding "\$\{P\}"|projects add-iam-policy-binding'
check "no keys or secrets created"           lacks 'keys create|secrets create|--set-secrets'

# Every role the script grants must be on the allowlist from the issue.
roles="$(grep -oE 'roles/[A-Za-z.]+' "${S}" | sort -u | tr '\n' ' ')"
check "role allowlist ($roles)" bash -c '
  allowed="roles/artifactregistry.writer roles/iam.serviceAccountUser roles/run.invoker roles/iap.httpsResourceAccessor"
  for r in '"$roles"'; do [[ " $allowed " == *" $r "* ]] || exit 1; done'

# Idempotency: every create sits behind a describe guard.
check "AR create guarded"                    has 'artifacts repositories describe .*\|\| *\\?$'
check "SA create guarded"                    has 'service-accounts describe .*\|\| *\\?$'
check "service deploy guarded"               has 'if ! gcloud run services describe'
check "GitHub variables set"                 bash -c "has() { grep -qE -- \"\$1\" ${S}; }; has 'IRROPS_DEMO_SERVICE' && has 'IRROPS_AR_REPO'"
check "deployer bindings limited to 2"       test "$(grep -c 'SA_DEPL}"' "${S}")" -eq 2

exit $((fails > 0))
