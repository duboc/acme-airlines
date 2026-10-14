#!/usr/bin/env bash
# Push the 6 scion-frontiers/agent-team role templates (factory/templates/*) to the hub
# at global scope, remove legacy acme-* templates, and prune idle standing worker sprawl
# so only the persistent coordinator (acme-lead) remains.
#
# Usage: infra/45-push-templates.sh                       # all 6 templates
#        infra/45-push-templates.sh coordinator developer # only the named ones
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
cd "${REPO_ROOT}"

TEMPLATES=("$@")
if (( ${#TEMPLATES[@]} == 0 )); then
  for d in factory/templates/*; do
    [[ -d "${d}" ]] && TEMPLATES+=("$(basename "${d}")")
  done
fi
for t in "${TEMPLATES[@]}"; do
  [[ "${t}" =~ ^[a-z-]+$ && -f "factory/templates/${t}/scion-agent.yaml" ]] \
    || { echo "not a template: ${t}" >&2; exit 1; }
done

bash "${INFRA_DIR}/30-hub-admin-access.sh" enable
trap 'bash "${INFRA_DIR}/30-hub-admin-access.sh" disable' EXIT

echo "==> Copying ${#TEMPLATES[@]} templates to the VM (~/.scion/templates/)"
vm_ssh "mkdir -p ~/.scion/templates && cd ~/.scion/templates && rm -rf ${TEMPLATES[*]} acme-*"
gcloud compute scp --recurse "${TEMPLATES[@]/#/factory/templates/}" "${VM_NAME}:~/.scion/templates/" \
  --project="${PROJECT_ID}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet

for t in "${TEMPLATES[@]}"; do
  echo "==> templates push ${t} (global scope)"
  hub_cli --global templates push "${t}"
done

echo "==> Removing obsolete acme-* templates and idle standing worker agents from the Hub"
vm_ssh "
  set -euo pipefail
  sudo python3 - <<'PY'
import json, sqlite3, subprocess

db = sqlite3.connect('/home/scion/.scion/hub.db')
cur = db.cursor()

# Switch acme-lead to the canonical coordinator template
coord_row = cur.execute(\"SELECT id FROM templates WHERE slug = 'coordinator' AND scope = 'global' LIMIT 1\").fetchone()
if coord_row:
    cur.execute(\"UPDATE agents SET template = 'coordinator' WHERE name = 'acme-lead'\")
    for aid, acfg_raw in cur.execute(\"SELECT id, applied_config FROM agents WHERE name = 'acme-lead'\").fetchall():
        if acfg_raw:
            acfg = json.loads(acfg_raw)
            acfg['template'] = 'coordinator'
            acfg['templateId'] = coord_row[0]
            cur.execute(\"UPDATE agents SET applied_config = ? WHERE id = ?\", (json.dumps(acfg), aid))

# Find idle standing acme-* worker agents (never acme-lead)
workers = [r[0] for r in cur.execute(\"SELECT name FROM agents WHERE name LIKE 'acme-%' AND name != 'acme-lead'\").fetchall()]
db.commit()
db.close()

for w in workers:
    print(f'deleting idle standing worker {w}...')
    subprocess.run(['sudo', '-u', 'scion', 'scion', '--hub', 'http://localhost:8080', '--project', '${HUB_PROJECT_SLUG}', 'delete', w, '--non-interactive'], check=False)
PY
  sudo sqlite3 /home/scion/.scion/hub.db \"DELETE FROM templates WHERE slug LIKE 'acme-%'; DELETE FROM agents WHERE name LIKE 'acme-%' AND name != 'acme-lead';\"
  sudo rm -rf /home/scion/.scion/templates/acme-*
  sudo chown -R scion:scion /home/scion/.scion
"

echo "done: pushed ${TEMPLATES[*]}"
