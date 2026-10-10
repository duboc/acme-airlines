#!/usr/bin/env bash
# Unit tests for infra/verify.sh helpers that don't need the cloud.
# Usage: infra/tests/test_verify.sh
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
# shellcheck source=infra/verify.sh
VERIFY_LIB_ONLY=1 source infra/verify.sh

fails=0
expect() {  # expect <0|1> <name> <input>
  if vertex_all_ok "$3"; then got=0; else got=1; fi
  if [[ "${got}" == "$1" ]]; then echo "ok   $2"; else echo "FAIL $2 (want $1, got ${got})"; fails=$((fails+1)); fi
}

expect 0 "all 200"            $'gemini g 200\nclaude a 200\nclaude b 200'
expect 1 "one 404"            $'gemini g 200\nclaude a 404\nclaude b 200'
expect 1 "missing code"       $'gemini g 200\nclaude a \nclaude b 200'
expect 1 "curl crash 000"     $'gemini g 200\nclaude a 000'
expect 1 "no result lines"    $'ssh: connection refused'
expect 1 "claude line absent" $'gemini g 200'

# topology_ok <rows "name|id|ancestry-json"> <min-children>
texpect() {  # texpect <0|1> <name> <min> <rows>
  if topology_ok "$4" "$3" >/dev/null; then got=0; else got=1; fi
  if [[ "${got}" == "$1" ]]; then echo "ok   $2"; else echo "FAIL $2 (want $1, got ${got})"; fails=$((fails+1)); fi
}
U=u-1; L=lead-1
texpect 0 "lead + children under lead"   2 "acme-lead|$L|[\"$U\"]
acme-backend|b-1|[\"$U\",\"$L\"]
acme-reviewer|r-1|[\"$U\",\"$L\"]"
texpect 0 "grandchild via eng-manager"   2 "acme-lead|$L|[\"$U\"]
acme-eng-manager|e-1|[\"$U\",\"$L\"]
acme-backend-2|b-2|[\"$U\",\"$L\",\"e-1\"]"
texpect 1 "orphan acme agent (user root)" 1 "acme-lead|$L|[\"$U\"]
acme-backend|b-1|[\"$U\",\"$L\"]
acme-qa|q-1|[\"$U\"]"
texpect 1 "no lead"                       0 "acme-backend|b-1|[\"$U\"]"
texpect 1 "too few standing children"     3 "acme-lead|$L|[\"$U\"]
acme-backend|b-1|[\"$U\",\"$L\"]"
texpect 0 "non-acme agents ignored"       1 "acme-lead|$L|[\"$U\"]
acme-backend|b-1|[\"$U\",\"$L\"]
smoke-x|s-1|[\"$U\"]"

# models_ok <dir>: every template's harness/model matches the addendum-B mapping
mexpect() {  # mexpect <0|1> <name> <dir>
  if models_ok "$3" >/dev/null; then got=0; else got=1; fi
  if [[ "${got}" == "$1" ]]; then echo "ok   $2"; else echo "FAIL $2 (want $1, got ${got})"; fails=$((fails+1)); fi
}
# caps_ok <dir>: every template except acme-lead has max_duration and max_turns
cexpect() {
  if caps_ok "$3" >/dev/null; then got=0; else got=1; fi
  if [[ "${got}" == "$1" ]]; then echo "ok   $2"; else echo "FAIL $2 (want $1, got ${got})"; fails=$((fails+1)); fi
}
T=$(mktemp -d); mk() { mkdir -p "$T/$1/$2"; printf '%b' "$3" > "$T/$1/$2/scion-agent.yaml"; }
mk good acme-lead 'default_harness_config: claude\nmodel: large\n'
mk good acme-backend 'default_harness_config: claude\nmodel: medium\nmax_duration: "2h"\nmax_turns: 400\n'
mk good acme-reviewer 'default_harness_config: antigravity\nmodel: medium\nmax_duration: "45m"\nmax_turns: 150\n'
mexpect 0 "models match mapping" "$T/good"
cexpect 0 "caps present (lead exempt)" "$T/good"
mk badmodel acme-reviewer 'default_harness_config: claude\nmodel: large\nmax_duration: "45m"\nmax_turns: 150\n'
mexpect 1 "reviewer on opus rejected" "$T/badmodel"
mk nocap acme-backend 'default_harness_config: claude\nmodel: medium\n'
cexpect 1 "missing caps rejected" "$T/nocap"
mexpect 1 "empty dir rejected" "$T/none"
rm -rf "$T"

kexpect() {  # kexpect <0|1> <cron>
  if cron_interval_ok "$2" 30; then got=0; else got=1; fi
  if [[ "${got}" == "$1" ]]; then echo "ok   cron '$2'"; else echo "FAIL cron '$2' (want $1, got ${got})"; fails=$((fails+1)); fi
}
kexpect 0 "*/30 * * * *"
kexpect 0 "0,30 * * * *"
kexpect 0 "0 * * * *"
kexpect 1 "*/10 * * * *"
kexpect 1 "* * * * *"
kexpect 1 "0,15,30,45 * * * *"
kexpect 1 ""

if [[ "${fails}" == 0 ]]; then echo "PASS test_verify"; else echo "FAIL test_verify (${fails})"; exit 1; fi
