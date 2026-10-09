#!/usr/bin/env bash
# Verification harness for the acme-airlines factory.
#
# Usage: infra/verify.sh <project|hub|vertex|claude-image|hubproject|smoke|all>
# Prints "PASS <check>" or "FAIL <check>: <reason>"; exits non-zero on failure.
set -uo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"

fail() { echo "FAIL $1: $2"; return 1; }
pass() { echo "PASS $1"; }

check_project()      { fail project "not implemented"; }
check_hub()          { fail hub "not implemented"; }
check_vertex()       { fail vertex "not implemented"; }
check_claude_image() { fail claude-image "not implemented"; }
check_hubproject()   { fail hubproject "not implemented"; }
check_smoke()        { fail smoke "not implemented"; }

run() {
  case "$1" in
    project)      check_project ;;
    hub)          check_hub ;;
    vertex)       check_vertex ;;
    claude-image) check_claude_image ;;
    hubproject)   check_hubproject ;;
    smoke)        check_smoke ;;
    all)
      local rc=0
      for c in project hub vertex claude-image hubproject smoke; do run "$c" || rc=1; done
      return "$rc" ;;
    *) echo "usage: $0 <project|hub|vertex|claude-image|hubproject|smoke|all>" >&2; return 2 ;;
  esac
}

run "${1:-all}"
