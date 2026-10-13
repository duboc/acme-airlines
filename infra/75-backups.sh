#!/usr/bin/env bash
# Backups for the hub VM (audit 2026-10-12): a daily snapshot schedule on the boot disk,
# which holds hub.db, agent homes and workspaces. Idempotent.
#
#   Policy   <VM_NAME>-daily-snapshots: daily at 03:00 UTC, kept 7 days, regional storage
#   Restore  create a disk from a snapshot, then a VM from that disk (docs/scion/05-cost-and-safety.md)
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"

POLICY="${VM_NAME}-daily-snapshots"
ZONE="$(resolve_zone)"

if ! gcloud compute resource-policies describe "${POLICY}" --region="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "==> Creating snapshot schedule ${POLICY}"
  gcloud compute resource-policies create snapshot-schedule "${POLICY}" \
    --project="${PROJECT_ID}" --region="${REGION}" \
    --daily-schedule --start-time=03:00 \
    --max-retention-days=7 --on-source-disk-delete=keep-auto-snapshots \
    --storage-location="${REGION}"
fi

if ! gcloud compute disks describe "${VM_NAME}" --zone="${ZONE}" --project="${PROJECT_ID}" \
       --format='value(resourcePolicies)' | grep -q "${POLICY}"; then
  echo "==> Attaching ${POLICY} to disk ${VM_NAME}"
  gcloud compute disks add-resource-policies "${VM_NAME}" --zone="${ZONE}" --project="${PROJECT_ID}" \
    --resource-policies="${POLICY}"
fi
echo "done: daily snapshots (03:00 UTC, 7-day retention) on ${VM_NAME}"
