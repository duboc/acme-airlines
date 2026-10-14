#!/usr/bin/env bash
# Factory control for both Scion projects (${BUILD_PROJECT} and ${EVAL_PROJECT}):
#
#   infra/60-factory.sh status          # VM state, heartbeat schedules, agents by project & phase
#   infra/60-factory.sh tick-install    # create/update the two 15-min heartbeat schedules (idempotent)
#   infra/60-factory.sh pause           # pause heartbeats + suspend all agents across both projects
#   infra/60-factory.sh resume          # resume heartbeats + wake lead and eval-lead
#
# Product-owner rule: the VM is never stopped by tooling; cost control = pausing agents.
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

BUILD_HB_NAME="${BUILD_PROJECT}-lead-heartbeat"
EVAL_HB_NAME="${EVAL_PROJECT}-lead-heartbeat"
HB_TEMPLATE="$(python3 "${INFRA_DIR}/render-templates.py" --file "${INFRA_DIR}/heartbeat-message.txt")"
BUILD_HB_MSG="${HB_TEMPLATE//<your project slug>/${BUILD_PROJECT}}"
EVAL_HB_MSG="${HB_TEMPLATE//<your project slug>/${EVAL_PROJECT}}"

admin_on()  { bash "${INFRA_DIR}/30-hub-admin-access.sh" enable >/dev/null; trap admin_off EXIT; }
admin_off() { bash "${INFRA_DIR}/30-hub-admin-access.sh" disable >/dev/null || true; }
vm_status() { gcloud compute instances describe "${VM_NAME}" --zone="$(resolve_zone)" --project="${PROJECT_ID}" --format='value(status)'; }

sched_id() {
  printf '%s\n' "SELECT id FROM schedules WHERE name='$1';" | \
    gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet \
      --command="sudo sqlite3 -readonly /home/scion/.scion/hub.db" 2>/dev/null | head -1
}

sched_cron() {
  printf '%s\n' "SELECT cron_expr FROM schedules WHERE name='$1';" | \
    gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet \
      --command="sudo sqlite3 -readonly /home/scion/.scion/hub.db" 2>/dev/null | head -1
}

ensure_schedule() {
  local proj="$1" name="$2" agent="$3" msg="$4" id cur_cron
  id="$(sched_id "${name}")"
  cur_cron="$(sched_cron "${name}")"
  if [[ -n "${id}" && "${cur_cron}" == "${HEARTBEAT_CRON}" ]]; then
    echo "schedule ${name} already exists (${cur_cron})"
  else
    if [[ -n "${id}" ]]; then
      echo "updating schedule ${name} from '${cur_cron}' to '${HEARTBEAT_CRON}'..."
      hub_cli --project "${proj}" schedule delete "${id}"
    fi
    hub_cli --project "${proj}" schedule create-recurring --name "${name}" --cron "${HEARTBEAT_CRON}" \
      --type message --agent "${agent}" --message "${msg}"
  fi
}

case "${1:-status}" in
  status)
    echo "VM: $(vm_status)"
    [[ "$(vm_status)" == "RUNNING" ]] || exit 0
    printf '%s\n' ".separator ' | '" \
      "SELECT 'schedule', name, cron_expr, status FROM schedules WHERE name IN ('${BUILD_HB_NAME}','${EVAL_HB_NAME}');" \
      "SELECT 'agents', p.slug, a.phase, count(*) FROM agents a JOIN projects p ON a.project_id = p.id WHERE a.deleted_at IS NULL GROUP BY p.slug, a.phase;" | \
      gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet \
        --command="sudo sqlite3 -readonly /home/scion/.scion/hub.db" 2>/dev/null
    for j in acme-factory-watchdog acme-lead-reincarnate; do
      st="$(gcloud scheduler jobs describe "${j}" --location="${REGION}" --project="${PROJECT_ID}" --format='value(state)' 2>/dev/null || echo 'absent')"
      echo "scheduler | ${j} | ${st}"
    done ;;
  tick-install)
    admin_on
    old_id="$(sched_id "acme-factory-tick")"
    [[ -z "${old_id}" ]] || hub_cli --project "${BUILD_PROJECT}" schedule delete "${old_id}" || true
    ensure_schedule "${BUILD_PROJECT}" "${BUILD_HB_NAME}" "lead" "${BUILD_HB_MSG}"
    ensure_schedule "${EVAL_PROJECT}"  "${EVAL_HB_NAME}"  "eval-lead" "${EVAL_HB_MSG}" ;;
  pause)
    admin_on
    for pair in "${BUILD_PROJECT}:${BUILD_HB_NAME}" "${EVAL_PROJECT}:${EVAL_HB_NAME}"; do
      p="${pair%%:*}"; n="${pair#*:}"
      id="$(sched_id "${n}")"; [[ -z "${id}" ]] || hub_cli --project "${p}" schedule pause "${id}" || true
      hub_cli --project "${p}" suspend --all || true
    done
    for j in acme-factory-watchdog acme-lead-reincarnate; do
      gcloud scheduler jobs pause "${j}" --location="${REGION}" --project="${PROJECT_ID}" --quiet 2>/dev/null || true
    done
    echo "paused: heartbeats + watchdog schedules paused, all agents suspended across ${BUILD_PROJECT} and ${EVAL_PROJECT}" ;;
  resume)
    admin_on
    for pair in "${BUILD_PROJECT}:${BUILD_HB_NAME}:lead:${BUILD_HB_MSG}" "${EVAL_PROJECT}:${EVAL_HB_NAME}:eval-lead:${EVAL_HB_MSG}"; do
      IFS=':' read -r p n lead_agent msg <<<"${pair}"
      id="$(sched_id "${n}")"; [[ -z "${id}" ]] || hub_cli --project "${p}" schedule resume "${id}" || true
      hub_cli --project "${p}" resume "${lead_agent}" "${msg}" 2>/dev/null \
        || hub_cli --project "${p}" message "${lead_agent}" "${msg}"
    done
    for j in acme-factory-watchdog acme-lead-reincarnate; do
      gcloud scheduler jobs resume "${j}" --location="${REGION}" --project="${PROJECT_ID}" --quiet 2>/dev/null || true
    done
    echo "resumed: heartbeats active, lead and eval-lead woken" ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
