#!/usr/bin/env bash
# Assert both images carry the commit SHA and the collector job still maps SCION_HUB_TOKEN.
# Also asserts HUB_PROJECT on the job equals the hub project UUID (the slug is rejected by the hub).
# Needs: AR_REPO, GITHUB_SHA, REGION, PROJECT, HUB_PROJECT.
set -uo pipefail
rc=0
svc=$(gcloud run services describe acme-observatory --region "$REGION" --project "$PROJECT" \
  --format='value(spec.template.spec.containers[0].image)') || rc=1
job_json=$(gcloud run jobs describe acme-observatory-collector --region "$REGION" \
  --project "$PROJECT" --format=json) || rc=1
job=$(jq -r '.spec.template.spec.template.spec.containers[0].image // ""' <<<"$job_json")
echo "service: $svc"
echo "job:     $job"
case "$svc" in *":$GITHUB_SHA") ;; *) echo "::error::dashboard image is not :$GITHUB_SHA"; rc=1 ;; esac
case "$job" in *":$GITHUB_SHA") ;; *) echo "::error::collector image is not :$GITHUB_SHA"; rc=1 ;; esac
if ! jq -e '.spec.template.spec.template.spec.containers[0].env // []
    | any(.name == "SCION_HUB_TOKEN" and has("valueFrom"))' <<<"$job_json" >/dev/null; then
  echo "::error::collector job no longer maps SCION_HUB_TOKEN"
  rc=1
fi
if ! jq -e --arg v "${HUB_PROJECT:?}" '.spec.template.spec.template.spec.containers[0].env // []
    | any(.name == "HUB_PROJECT" and .value == $v)' <<<"$job_json" >/dev/null; then
  echo "::error::collector job HUB_PROJECT is not the hub project UUID ($HUB_PROJECT)"
  rc=1
fi
exit $rc
