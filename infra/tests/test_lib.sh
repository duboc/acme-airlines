#!/usr/bin/env bash
# Regression test: hub_cli must return the remote command's status under `set -euo pipefail`
# (bug: an empty HUB_CLI_STDIN made the stdin brace-group return 1, failing every call).
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1
STUB="$(mktemp -d)"; trap 'rm -rf "${STUB}"' EXIT
cat > "${STUB}/gcloud" <<'EOF'
#!/usr/bin/env bash
# stub: identity token -> fake; compute ssh -> consume stdin, exit with $STUB_RC
case "$*" in
  *print-identity-token*) echo "fake-token" ;;
  *"compute ssh"*) cat >/dev/null; exit "${STUB_RC:-0}" ;;
  *"compute instances list"*) echo "us-central1-a" ;;
  *) exit 0 ;;
esac
EOF
chmod +x "${STUB}/gcloud"
export PATH="${STUB}:${PATH}"
# shellcheck source=infra/lib.sh
source infra/lib.sh

fails=0
check() {  # check <name> <expected-rc> <cmd...>
  local name="$1" want="$2"; shift 2
  local got=0; ( set -euo pipefail; "$@" ) >/dev/null 2>&1 || got=$?
  if [[ "${got}" == "${want}" ]]; then echo "ok   ${name}"; else echo "FAIL ${name} (want rc=${want}, got ${got})"; fails=$((fails+1)); fi
}
check "hub_cli success, no extra stdin"        0 env STUB_RC=0 bash -c 'set -euo pipefail; source infra/lib.sh; hub_cli hub status'
check "hub_cli success, with HUB_CLI_STDIN"    0 env STUB_RC=0 HUB_CLI_STDIN=x bash -c 'set -euo pipefail; source infra/lib.sh; hub_cli hub status'
check "hub_cli propagates remote failure"      7 env STUB_RC=7 bash -c 'set -euo pipefail; source infra/lib.sh; hub_cli hub status'
if [[ "${fails}" == 0 ]]; then echo "PASS test_lib"; else echo "FAIL test_lib (${fails})"; exit 1; fi
