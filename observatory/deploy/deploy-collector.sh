#!/usr/bin/env bash
# Deploy the collector job image, mapping GITHUB_TOKEN only if its secret has a usable version.
# Needs: IMG, REGION, PROJECT. Never uses --set-secrets/--clear-secrets (SCION_HUB_TOKEN must stay).
set -uo pipefail

SECRET=acme-observatory-github-token
MAP="GITHUB_TOKEN=${SECRET}:latest"
common=(--image "$IMG" --region "$REGION" --project "$PROJECT" --quiet)

deploy_without_token() {
  echo "::warning::Deploying the collector without GITHUB_TOKEN; the GitHub snapshot is skipped until ${SECRET} has an enabled version."
  gcloud run jobs deploy acme-observatory-collector "${common[@]}" --remove-secrets=GITHUB_TOKEN
}

# 1. Optional probe. The deployer usually lacks Secret Manager access, so denial is expected.
mode=try
if versions=$(gcloud secrets versions list "$SECRET" --project "$PROJECT" \
  --filter="state=ENABLED" --format="value(name)" --limit=1 2>/dev/null); then
  if [ -z "$versions" ]; then
    mode=skip
  fi
fi

if [ "$mode" = skip ]; then
  deploy_without_token
  exit $?
fi

# 2. Deploy with the secret. Only a "Secret .../${SECRET}/... was not found" error falls back; anything else fails.
log=$(mktemp)
if gcloud run jobs deploy acme-observatory-collector "${common[@]}" --update-secrets="$MAP" 2>&1 | tee "$log"; then
  exit 0
fi
if grep -qE "Secret [^ ]*secrets/${SECRET}/[^ ]* was not found" "$log"; then
  deploy_without_token  # 3.
  exit $?
fi
echo "::error::Collector deploy failed for a reason other than a missing secret version."
exit 1
