#!/usr/bin/env bash
# Set a project-scoped hub secret, reading the VALUE from this script's stdin.
# The value travels over IAP SSH stdin to the VM and is POSTed to the Hub API
# from memory (curl -d @-). It is never placed in argv, files, or logs.
# Requires infra/30-hub-admin-access.sh enable.
#
# Usage: printf '%s' "$VALUE" | infra/31-set-project-secret.sh <project-id> <KEY>
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

PROJECT_UUID="${1:?project id}"; KEY="${2:?secret key}"
VALUE="$(cat)"; [[ -n "${VALUE}" ]] || { echo "empty value on stdin" >&2; exit 1; }
TOK="$(gcloud auth print-identity-token 2>/dev/null)"

{ printf '%s\n' "${TOK}"; printf '%s' "${VALUE}"; } | \
  gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" \
    --tunnel-through-iap --quiet --command="
      set -euo pipefail
      IFS= read -r T; V=\$(cat)
      umask 077; H=\$(mktemp -p /dev/shm); trap 'rm -f \"\$H\"' EXIT
      printf 'Authorization: Bearer %s\n' \"\$T\" > \"\$H\"
      V=\"\$V\" python3 -c 'import json,os;print(json.dumps({\"value\":os.environ[\"V\"],\"encoding\":\"raw\",\"scope\":\"project\",\"scopeId\":\"${PROJECT_UUID}\",\"injectionMode\":\"always\",\"type\":\"environment\",\"description\":\"GitHub fine-grained PAT for duboc/acme-airlines\"}))' | \
        curl -sS -o /dev/null -w 'HTTP %{http_code}\n' -X PUT -H @\"\$H\" -H 'Content-Type: application/json' \
          --data-binary @- 'http://localhost:8080/api/v1/secrets/${KEY}'"
