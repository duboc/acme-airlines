#!/usr/bin/env bash
# Factory control: keep everything intact while turning cost on or off.
#
#   infra/60-factory.sh status          # VM state, tick schedule, agents by phase
#   infra/60-factory.sh tick-install    # create the recurring TICK schedule (idempotent)
#   infra/60-factory.sh pause           # pause TICK + suspend all agents (sessions/roster/graph kept)
#   infra/60-factory.sh resume          # resume TICK + wake acme-lead
#
# Product-owner rule: the VM is never stopped by tooling; cost control = pausing agents.
#
# Hub operations temporarily enable operator admin access
# (infra/30-hub-admin-access.sh) and always disable it again on exit.
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

PROJ_SLUG="${HUB_PROJECT_SLUG}"
TICK_NAME="acme-factory-tick"
TICK_MSG="TICK: run one factory loop pass (see the factory loop section of your instructions). If nothing changed since your last pass, only refresh the status issue timestamp."

admin_on()  { bash "${INFRA_DIR}/30-hub-admin-access.sh" enable >/dev/null; trap admin_off EXIT; }
admin_off() { bash "${INFRA_DIR}/30-hub-admin-access.sh" disable >/dev/null || true; }
hub()       { hub_cli --project "${PROJ_SLUG}" "$@"; }
vm_status() { gcloud compute instances describe "${VM_NAME}" --zone="$(resolve_zone)" --project="${PROJECT_ID}" --format='value(status)'; }

tick_id() {  # prints the schedule id for TICK_NAME, or nothing
  printf '%s\n' "SELECT id FROM schedules WHERE name='${TICK_NAME}';" | \
    gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet \
      --command="sudo sqlite3 -readonly /home/scion/.scion/hub.db" 2>/dev/null | head -1
}

tick_cron() {  # prints the cron_expr for TICK_NAME, or nothing
  printf '%s\n' "SELECT cron_expr FROM schedules WHERE name='${TICK_NAME}';" | \
    gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet \
      --command="sudo sqlite3 -readonly /home/scion/.scion/hub.db" 2>/dev/null | head -1
}

case "${1:-status}" in
  status)
    echo "VM: $(vm_status)"
    [[ "$(vm_status)" == "RUNNING" ]] || exit 0
    printf '%s\n' ".separator ' | '" \
      "SELECT 'schedule', name, cron_expr, status FROM schedules WHERE name='${TICK_NAME}';" \
      "SELECT 'agents', phase, count(*) FROM agents WHERE deleted_at IS NULL GROUP BY phase;" | \
      gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet \
        --command="sudo sqlite3 -readonly /home/scion/.scion/hub.db" 2>/dev/null
    for j in acme-factory-watchdog acme-lead-reincarnate; do
      st="$(gcloud scheduler jobs describe "${j}" --location="${REGION}" --project="${PROJECT_ID}" --format='value(state)' 2>/dev/null || echo 'absent')"
      echo "scheduler | ${j} | ${st}"
    done ;;
  tick-install)
    admin_on
    id="$(tick_id)"
    cur_cron="$(tick_cron)"
    if [[ -n "${id}" && "${cur_cron}" == "${TICK_CRON}" ]]; then
      echo "schedule ${TICK_NAME} already exists (${cur_cron})"
    else
      if [[ -n "${id}" ]]; then
        echo "updating schedule ${TICK_NAME} from '${cur_cron}' to '${TICK_CRON}'..."
        hub schedule delete "${id}"
      fi
      hub schedule create-recurring --name "${TICK_NAME}" --cron "${TICK_CRON}" \
        --type message --agent acme-lead --message "${TICK_MSG}"
    fi ;;
  pause)
    admin_on
    id="$(tick_id)"; [[ -z "${id}" ]] || hub schedule pause "${id}" || true
    for j in acme-factory-watchdog acme-lead-reincarnate; do
      gcloud scheduler jobs pause "${j}" --location="${REGION}" --project="${PROJECT_ID}" --quiet 2>/dev/null || true
    done
    hub suspend --all || true
    echo "paused: tick + watchdog schedules paused, agents suspended (roster and graph intact)" ;;
  resume)
    admin_on
    id="$(tick_id)"; [[ -z "${id}" ]] || hub schedule resume "${id}" || true
    for j in acme-factory-watchdog acme-lead-reincarnate; do
      gcloud scheduler jobs resume "${j}" --location="${REGION}" --project="${PROJECT_ID}" --quiet 2>/dev/null || true
    done
    hub resume acme-lead "Factory resumed by the operator. Run a full factory loop pass now." 2>/dev/null \
      || hub message acme-lead "Factory resumed by the operator. Run a full factory loop pass now."
    echo "resumed: tick + watchdog schedules active, acme-lead woken" ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
