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

if [[ "${fails}" == 0 ]]; then echo "PASS test_verify"; else echo "FAIL test_verify (${fails})"; exit 1; fi
