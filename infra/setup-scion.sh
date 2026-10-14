#!/usr/bin/env bash
# Recreate and sync the Scion Hub configuration, harness model_aliases, and the
# 6 scion-frontiers/agent-team templates (docs/scion/templates/) using the native
# `scion` CLI. Idempotent.
#
# Usage:
#   bash infra/setup-scion.sh                # full setup (harnesses, templates, env, SA, coordinator)
#   GITHUB_PAT=ghp_... bash infra/setup-scion.sh  # also rotates the project GITHUB_TOKEN secret
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

AGY_VERSION="1.3.4"
TEMPLATES_DIR="${REPO_ROOT}/docs/scion/templates"
AGENT_SA_EMAIL="${FACTORY_PREFIX}-agent@${PROJECT_ID}.iam.gserviceaccount.com"

echo "==> 1. Ensuring Vertex AI IAM and harness images (Claude Code + Antigravity ${AGY_VERSION})"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${VM_SA}" --role=roles/aiplatform.user \
  --condition=None --format=none

vm_ssh "
  set -euo pipefail
  if ! sudo docker image inspect localhost/scion/scion-claude:latest >/dev/null 2>&1; then
    cd /opt/scion-source
    sudo docker build -t scion-claude:latest --build-arg BASE_IMAGE=scion-base:latest \
      -f harnesses/claude/Dockerfile harnesses/claude/
    sudo docker tag scion-claude:latest localhost/scion/scion-claude:latest
  fi
  cur_agy=\$(sudo docker run --rm --entrypoint agy scion-antigravity:latest --version 2>/dev/null || true)
  if [[ \"\${cur_agy}\" != \"${AGY_VERSION}\" ]]; then
    sudo docker build -t scion-antigravity:latest - <<'EOF'
FROM scion-antigravity:latest
USER root
RUN curl -fsSL -o /tmp/cli.tar.gz \"https://github.com/google-antigravity/antigravity-cli/releases/download/${AGY_VERSION}/agy_cli_linux_x64.tar.gz\" \\
  && tar -xzf /tmp/cli.tar.gz -C /tmp antigravity \\
  && mv /tmp/antigravity /usr/local/bin/agy \\
  && chmod +x /usr/local/bin/agy \\
  && rm /tmp/cli.tar.gz
USER scion
EOF
    sudo docker tag scion-antigravity:latest localhost/scion/scion-antigravity:latest
  fi
"

echo "==> 2. Enabling temporary operator federation on Hub (${VM_NAME})"
bash "${INFRA_DIR}/30-hub-admin-access.sh" enable
trap 'bash "${INFRA_DIR}/30-hub-admin-access.sh" disable' EXIT

echo "==> 3. Syncing harness model_aliases (Gemini 4 Argon, Flash 3.8, Opus 5.5, Sonnet 5.5) via 'scion harness-config sync'"
vm_ssh '
  set -euo pipefail
  sudo python3 - <<\PY
import json, pathlib, re

aliases_map = {
    "claude": {
        "small": "claude-haiku-5-5",
        "medium": "claude-sonnet-5-5",
        "large": "claude-opus-5-5",
        "extra-large": "claude-opus-5-5",
    },
    "antigravity": {
        "small": "Gemini 3.1 Flash Lite",
        "medium": "Gemini 3.8 Flash (Medium)",
        "large": "Gemini 4 Argon (High)",
        "extra-large": "Gemini 4 Argon (High)",
    },
    "gemini-cli": {
        "small": "gemini-3.5-flash-lite",
        "medium": "gemini-3.8-flash",
        "large": "gemini-4-argon",
        "extra-large": "gemini-4-argon",
    },
}

for hc_name, aliases in aliases_map.items():
    for root in [pathlib.Path("/home/scion/.scion/harness-configs") / hc_name,
                 pathlib.Path("/opt/scion-source/harnesses") / hc_name]:
        cfg = root / "config.yaml"
        if cfg.exists():
            text = cfg.read_text()
            block = "model_aliases:\n" + "".join(f"  {k}: {json.dumps(v)}\n" for k, v in aliases.items())
            new_text, n = re.subn(r"(?m)^model_aliases:\n(?:  [a-z-]+:.*\n)+", block, text, count=1)
            if n:
                cfg.write_text(new_text)
PY
  sudo chown -R scion:scion /home/scion/.scion/harness-configs
  mkdir -p ~/.scion/harness-configs
  sudo cp -r /home/scion/.scion/harness-configs/{claude,antigravity,gemini-cli} ~/.scion/harness-configs/
  sudo chown -R "$(id -un):$(id -gn)" ~/.scion
'

for hc in claude antigravity gemini-cli; do
  hub_cli --global harness-config sync "${hc}"
done

echo "==> 4. Importing and syncing the 6 templates from docs/scion/templates/ via 'scion templates import' + 'sync'"
vm_ssh "rm -rf /tmp/scion-templates ~/.scion/templates && mkdir -p /tmp/scion-templates ~/.scion/templates"
gcloud compute scp --recurse "${TEMPLATES_DIR}/"* "${VM_NAME}:/tmp/scion-templates/" \
  --project="${PROJECT_ID}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet

hub_cli --global templates import --all --force /tmp/scion-templates
hub_cli --global templates sync --all
vm_ssh "rm -rf /tmp/scion-templates"

echo "==> 5. Configuring Hub env vars, project secrets, and service account via 'scion' CLI"
hub_cli hub env set --scope=hub --always "GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
hub_cli hub env set --scope=hub --always "GOOGLE_CLOUD_LOCATION=${VERTEX_LOCATION:-global}"

if [[ -n "${GITHUB_PAT:-}" ]]; then
  hub_cli hub secret set "--project=${HUB_PROJECT_SLUG}" GITHUB_TOKEN "${GITHUB_PAT}"
fi

SA_ID="$(hub_cli --global service-accounts list --json 2>/dev/null \
  | python3 -c "import json,sys; d=json.load(sys.stdin); items=d if isinstance(d,list) else d.get('items',[]); print(next((x['id'] for x in items if x.get('email')=='${AGENT_SA_EMAIL}'),''))" || true)"
if [[ -z "${SA_ID}" ]]; then
  hub_cli --global service-accounts add "${AGENT_SA_EMAIL}" --gcp-project "${PROJECT_ID}" --name "ACME Agent SA" || true
  SA_ID="$(hub_cli --global service-accounts list --json 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); items=d if isinstance(d,list) else d.get('items',[]); print(next((x['id'] for x in items if x.get('email')=='${AGENT_SA_EMAIL}'),''))" || true)"
fi
[[ -n "${SA_ID}" ]] && hub_cli --global service-accounts verify "${SA_ID}" || true

echo "==> 6. Ensuring persistent coordinator (acme-lead) and recurring schedule via 'scion' CLI"
if ! hub_cli --project "${HUB_PROJECT_SLUG}" list --format json | python3 -c "import json,sys; d=json.load(sys.stdin); items=d if isinstance(d,list) else d.get('agents',[]); sys.exit(0 if any(a.get('name')=='acme-lead' for a in items) else 1)"; then
  hub_cli --project "${HUB_PROJECT_SLUG}" start acme-lead --type coordinator --role full \
    "You are acme-lead, the persistent coordinator for ${GITHUB_REPO}. Read AGENTS.md and run a factory loop pass."
fi

bash "${INFRA_DIR}/60-factory.sh" tick-install
echo "==> Done: Scion Hub, harness configs, and 6 templates synced via scion CLI."
