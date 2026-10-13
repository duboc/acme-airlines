#!/usr/bin/env bash
# Least-privilege agent GCP identity (`assign` mode) + Scion telemetry export + IAM hardening.
# Idempotent. Run after 20-deploy-hub.sh and 40-build-team-templates.sh (and again after 80/90 to
# scope deployer roles if re-applied).
#
# What it does:
#   1. Creates dedicated low-privilege SA `acme-agent@${PROJECT_ID}.iam.gserviceaccount.com` with ONLY:
#        roles/aiplatform.user, roles/logging.logWriter, roles/monitoring.metricWriter, roles/cloudtrace.agent
#   2. Grants the Hub VM SA (`${VM_NAME}@...`) `roles/iam.serviceAccountTokenCreator` on `acme-agent@`
#      so the Hub metadata sidecar can mint access tokens for assigned agents without holding
#      project-wide `roles/iam.serviceAccountAdmin`.
#   3. Removes excess project-level IAM bindings flagged by the security audit:
#        - `roles/iam.serviceAccountAdmin` on `${VM_NAME}@`
#        - project-wide `roles/run.developer` on `acme-observatory-deployer@` (replaced by project
#          `roles/run.viewer` + resource-level `roles/run.developer` on the 2 services + 1 job)
#        - project-wide `roles/iap.httpsResourceAccessor` on `user:${ADMIN_EMAIL}`
#   4. Sets `default_gcp_identity_mode: block` and enables Scion Cloud Monitoring telemetry export
#      (`telemetry.cloud`) in `/home/scion/.scion/settings.yaml`.
#   5. Registers + verifies `acme-agent@` on Hub project `${HUB_PROJECT_SLUG}`, sets project-default
#      GCP identity to `assign` (`acme-agent@`), updates suspended standing agents' `gcpIdentity`
#      in `hub.db`, and reincarnates `acme-lead` onto `acme-agent@` when idle.
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

P="${PROJECT_ID}"
R="${REGION}"
AGENT_SA_NAME="acme-agent"
AGENT_SA="${AGENT_SA_NAME}@${P}.iam.gserviceaccount.com"
VM_SA="${VM_NAME}@${P}.iam.gserviceaccount.com"
DEPL_SA="acme-observatory-deployer@${P}.iam.gserviceaccount.com"
SETTINGS="/home/scion/.scion/settings.yaml"
BEGIN_TEL="# BEGIN acme-telemetry (infra/25-agent-identity.sh)"
END_TEL="# END acme-telemetry"

log() { printf '\n==> %s\n' "$*"; }

log "1. Dedicated agent service account: ${AGENT_SA}"
gcloud iam service-accounts describe "${AGENT_SA}" --project="${P}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${AGENT_SA_NAME}" --project="${P}" \
    --display-name="ACME Scion agent runtime (least-privilege)"

for role in roles/aiplatform.user roles/logging.logWriter roles/monitoring.metricWriter roles/cloudtrace.agent; do
  gcloud projects add-iam-policy-binding "${P}" \
    --member="serviceAccount:${AGENT_SA}" --role="${role}" --condition=None --format=none
done

log "2. Grant Hub VM SA (${VM_SA}) tokenCreator on ${AGENT_SA} and admin actAs"
gcloud iam service-accounts add-iam-policy-binding "${AGENT_SA}" --project="${P}" \
  --member="serviceAccount:${VM_SA}" --role="roles/iam.serviceAccountTokenCreator" --format=none
gcloud iam service-accounts add-iam-policy-binding "${AGENT_SA}" --project="${P}" \
  --member="user:${ADMIN_EMAIL}" --role="roles/iam.serviceAccountUser" --format=none

log "3. Remove excess project-level IAM bindings"
gcloud projects remove-iam-policy-binding "${P}" \
  --member="serviceAccount:${VM_SA}" --role="roles/iam.serviceAccountAdmin" --all --quiet >/dev/null 2>&1 || true

gcloud projects remove-iam-policy-binding "${P}" \
  --member="user:${ADMIN_EMAIL}" --role="roles/iap.httpsResourceAccessor" --all --quiet >/dev/null 2>&1 || true

if gcloud iam service-accounts describe "${DEPL_SA}" --project="${P}" >/dev/null 2>&1; then
  gcloud projects add-iam-policy-binding "${P}" \
    --member="serviceAccount:${DEPL_SA}" --role="roles/run.viewer" --condition=None --format=none
  for svc in acme-observatory acme-irrops-demo; do
    if gcloud run services describe "${svc}" --region="${R}" --project="${P}" >/dev/null 2>&1; then
      gcloud run services add-iam-policy-binding "${svc}" --region="${R}" --project="${P}" \
        --member="serviceAccount:${DEPL_SA}" --role="roles/run.developer" --format=none
    fi
  done
  if gcloud run jobs describe acme-observatory-collector --region="${R}" --project="${P}" >/dev/null 2>&1; then
    gcloud run jobs add-iam-policy-binding acme-observatory-collector --region="${R}" --project="${P}" \
      --member="serviceAccount:${DEPL_SA}" --role="roles/run.developer" --format=none
  fi
  gcloud projects remove-iam-policy-binding "${P}" \
    --member="serviceAccount:${DEPL_SA}" --role="roles/run.developer" --all --quiet >/dev/null 2>&1 || true
fi

log "4. Configure Hub settings.yaml: default_gcp_identity_mode=block + telemetry.cloud"
vm_ssh "sudo python3 - '${SETTINGS}' '${BEGIN_TEL}' '${END_TEL}' '${P}' <<'PY'
import re, sys
path, begin, end, proj = sys.argv[1:]
txt = open(path).read()
# Replace default_gcp_identity_mode: <anything> with block
if re.search(r'(?m)^default_gcp_identity_mode:\s*\S+', txt):
    txt = re.sub(r'(?m)^default_gcp_identity_mode:\s*\S+', 'default_gcp_identity_mode: block', txt)
else:
    txt = 'default_gcp_identity_mode: block\n' + txt
# Remove existing telemetry block if present
txt = re.sub(re.escape(begin) + r'.*?' + re.escape(end) + r'\n?', '', txt, flags=re.S)
block = (
    f'{begin}\n'
    f'telemetry:\n'
    f'  enabled: true\n'
    f'  cloud:\n'
    f'    enabled: true\n'
    f'    gcp_project_id: \"{proj}\"\n'
    f'    endpoint: \"monitoring.googleapis.com:443\"\n'
    f'    protocol: \"grpc\"\n'
    f'  hub:\n'
    f'    enabled: true\n'
    f'    report_interval: \"30s\"\n'
    f'{end}\n'
)
# Insert top-level telemetry block BEFORE server: so settings.yaml still ends inside server:
# (required by 30-hub-admin-access.sh, which appends federation: at EOF under server:).
if re.search(r'(?m)^server:\s*$', txt):
    txt = re.sub(r'(?m)^server:\s*$', block + 'server:', txt, count=1)
else:
    txt = block + txt
open(path, 'w').write(txt)
PY
sudo chown scion:scion '${SETTINGS}'"

log "5. Register & verify ${AGENT_SA} in Hub project ${HUB_PROJECT_SLUG} and set project default=assign"
(
  bash "${INFRA_DIR}/30-hub-admin-access.sh" enable >/dev/null
  trap 'bash "${INFRA_DIR}/30-hub-admin-access.sh" disable >/dev/null || true' EXIT

  PROJ_UUID="$(vm_ssh "sudo -u scion sqlite3 /home/scion/.scion/hub.db \"SELECT id FROM projects WHERE slug='${HUB_PROJECT_SLUG}';\"")"
  [[ -n "${PROJ_UUID}" ]] || { echo "ERROR: Hub project ${HUB_PROJECT_SLUG} not found" >&2; exit 1; }

  # Register SA, verify impersonation, and set project-default GCP identity via Hub REST API
  # (token passed over stdin, never in argv)
  SA_ID="$(gcloud auth print-identity-token 2>/dev/null | \
    gcloud compute ssh "${VM_NAME}" --project="${P}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet --command="
      set -euo pipefail
      IFS= read -r TOK
      HDR=\"Authorization: Bearer \${TOK}\"
      BASE=\"http://localhost:8080/api/v1/projects/${PROJ_UUID}\"
      SA_LIST=\$(curl -fsS -H \"\${HDR}\" \"\${BASE}/gcp-service-accounts\")
      SA_ID=\$(jq -r --arg e '${AGENT_SA}' '.items[]? | select(.email==\$e) | .id' <<<\"\${SA_LIST}\" | head -1)
      if [[ -z \"\${SA_ID}\" ]]; then
        CREATE_RESP=\$(jq -nc --arg e '${AGENT_SA}' --arg p '${P}' --arg n '${AGENT_SA_NAME}' \
          '{email:\$e, projectId:\$p, displayName:\$n}' \
          | curl -fsS -X POST -H \"\${HDR}\" -H 'Content-Type: application/json' --data-binary @- \"\${BASE}/gcp-service-accounts\")
        SA_ID=\$(jq -r '.id // empty' <<<\"\${CREATE_RESP}\")
      fi
      [[ -n \"\${SA_ID}\" ]] || { echo 'ERROR: could not resolve SA_ID' >&2; exit 1; }
      curl -fsS -X POST -H \"\${HDR}\" \"\${BASE}/gcp-service-accounts/\${SA_ID}/verify\" >/dev/null
      curl -fsS -H \"\${HDR}\" \"\${BASE}/settings\" \
        | jq --arg id \"\${SA_ID}\" '.defaultGCPIdentityMode=\"assign\" | .defaultGCPIdentityServiceAccountID=\$id' \
        | curl -fsS -X PUT -H \"\${HDR}\" -H 'Content-Type: application/json' --data-binary @- \"\${BASE}/settings\" >/dev/null
      printf '%s\n' \"\${SA_ID}\"
    ")"
  [[ -n "${SA_ID}" ]] || { echo "ERROR: could not resolve Hub service account ID for ${AGENT_SA}" >&2; exit 1; }
  echo "Hub service account ID: ${SA_ID} (verified, project default=assign)"

  # Update non-running agents' stored AppliedConfig.GCPIdentity in hub.db so waking them uses assign mode
  vm_ssh "sudo -u scion sqlite3 /home/scion/.scion/hub.db \"
    UPDATE agents
       SET applied_config = json_set(
             applied_config,
             '\\\$.gcpIdentity',
             json_object(
               'metadataMode', 'assign',
               'serviceAccountId', '${SA_ID}',
               'serviceAccountEmail', '${AGENT_SA}',
               'projectId', '${P}'
             )
           )
     WHERE deleted_at IS NULL
       AND slug != 'acme-lead'
       AND COALESCE(json_extract(applied_config, '\\\$.gcpIdentity.metadataMode'), '') != 'assign';
  \""

  # Check if acme-lead needs reincarnation onto acme-agent@
  LEAD_MODE="$(vm_ssh "sudo -u scion sqlite3 /home/scion/.scion/hub.db \"SELECT COALESCE(json_extract(applied_config, '\\\$.gcpIdentity.metadataMode'),'') FROM agents WHERE slug='acme-lead' AND deleted_at IS NULL;\"")"
  if [[ "${LEAD_MODE}" != "assign" || "${REINCARNATE_LEAD:-0}" == "1" ]]; then
    echo "Waiting for acme-lead to be idle before reincarnating onto ${AGENT_SA}..."
    for _ in $(seq 1 30); do
      ACT="$(vm_ssh "sudo -u scion sqlite3 /home/scion/.scion/hub.db \"SELECT lower(COALESCE(activity,'')) FROM agents WHERE slug='acme-lead' AND deleted_at IS NULL;\"")"
      if [[ "${ACT}" == "blocked" || "${ACT}" == "waiting_for_input" || "${ACT}" == "idle" || -z "${ACT}" ]]; then
        break
      fi
      sleep 10
    done
    STATUS_BODY="$(gh issue list --repo "${GITHUB_REPO}" --state open --search '"🏭 Factory status" in:title' --json number,body --jq '.[0].body // "See pinned Factory status issue."' 2>/dev/null || echo 'See pinned Factory status issue.')"
    HANDOFF_TMP="$(mktemp)"
    cat >"${HANDOFF_TMP}" <<EOF
# Reincarnation Handoff — acme-lead

## Current Role and Assignment
- Role: Factory orchestrator (\`acme-lead\`), running under least-privilege GCP service account \`${AGENT_SA}\` (\`assign\` mode).
- Repository: \`${GITHUB_REPO}\` (Hub project \`${HUB_PROJECT_SLUG}\`).

## Progress Summary
${STATUS_BODY}

## Active Work and Open Questions
- None blocking reincarnation; standing roster is preserved in Hub project \`${HUB_PROJECT_SLUG}\`.

## Next Steps
1. Run one factory loop pass: check \`scion list\`, \`gh issue list\`, and \`gh pr list\`.
2. Update the pinned **"🏭 Factory status"** issue timestamp and wait with \`sciontool status blocked "factory loop: waiting for workers or next TICK"\`.
EOF
    R_HANDOFF="/tmp/acme-lead-handoff.$$.md"
    gcloud compute scp --project="${P}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet "${HANDOFF_TMP}" "${VM_NAME}:${R_HANDOFF}"
    rm -f "${HANDOFF_TMP}"
    vm_ssh "chmod 0644 '${R_HANDOFF}'"
    hub_cli --project "${HUB_PROJECT_SLUG}" reincarnate acme-lead --service-account "${SA_ID}" --handoff-file "${R_HANDOFF}" --non-interactive
    vm_ssh "rm -f '${R_HANDOFF}'"
    # Wait up to 60s for the reincarnated container to come up
    for _ in $(seq 1 12); do
      if vm_ssh "sudo docker ps --format '{{.Names}}' | grep -q 'acme-lead'" >/dev/null 2>&1; then
        break
      fi
      sleep 5
    done
  fi
)

[[ "$(bash "${INFRA_DIR}/30-hub-admin-access.sh" status 2>/dev/null | tail -1)" == "disabled" ]] \
  || { echo "ERROR: admin access still enabled; run: infra/30-hub-admin-access.sh disable" >&2; exit 1; }
log "done: agent identity (assign mode: ${AGENT_SA}) and telemetry configured"
