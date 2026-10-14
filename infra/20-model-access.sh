#!/usr/bin/env bash
# Task 4 — Vertex AI (global) access, harness images (Claude Code + Antigravity 1.3.4),
# and harness model_aliases (Gemini 4 Argon, Gemini 3.8 Flash, Claude Opus 5.5, Claude Sonnet 5.5).
# Idempotent.
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

AGY_VERSION="1.3.4"

echo "==> Ensuring roles/aiplatform.user for ${VM_SA}"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${VM_SA}" --role=roles/aiplatform.user \
  --condition=None --format=none

echo "==> Building/upgrading scion-claude and scion-antigravity (agy ${AGY_VERSION}) on the VM"
vm_ssh "
  set -euo pipefail
  if ! sudo docker image inspect localhost/scion/scion-claude:latest >/dev/null 2>&1; then
    cd /opt/scion-source
    sudo docker build -t scion-claude:latest \
      --build-arg BASE_IMAGE=scion-base:latest \
      -f harnesses/claude/Dockerfile harnesses/claude/
    sudo docker tag scion-claude:latest localhost/scion/scion-claude:latest
  fi

  cur_agy=\$(sudo docker run --rm --entrypoint agy scion-antigravity:latest --version 2>/dev/null || true)
  if [[ \"\${cur_agy}\" != \"${AGY_VERSION}\" ]]; then
    echo \"Upgrading scion-antigravity:latest from agy \${cur_agy:-unknown} to ${AGY_VERSION}...\"
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
  else
    echo \"scion-antigravity already at agy ${AGY_VERSION}\"
  fi
"

echo "==> Configuring harness model_aliases on the VM (Gemini 4 Argon, Flash 3.8, Opus 5.5, Sonnet 5.5)"
vm_ssh '
  set -euo pipefail
  sudo python3 - <<\PY
import json, pathlib, re, sqlite3

home = pathlib.Path("/home/scion/.scion")

def patch_aliases(hc_name: str, aliases: dict[str, str]) -> None:
    for root in [home / "harness-configs" / hc_name, pathlib.Path("/opt/scion-source/harnesses") / hc_name]:
        cfg = root / "config.yaml"
        if not cfg.exists():
            continue
        text = cfg.read_text()
        block = "model_aliases:\n" + "".join(f"  {k}: {json.dumps(v)}\n" for k, v in aliases.items())
        new_text, n = re.subn(r"(?m)^model_aliases:\n(?:  [a-z-]+:.*\n)+", block, text, count=1)
        if n:
            cfg.write_text(new_text)

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

for name, amap in aliases_map.items():
    patch_aliases(name, amap)

# Also update stored harness_configs and acme-lead applied_config in hub.db if present
db = home / "hub.db"
if db.exists():
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    for name, amap in aliases_map.items():
        rows = cur.execute("SELECT id, storage_path, config FROM harness_configs WHERE slug = ?", (name,)).fetchall()
        for hc_id, spath, cfg_raw in rows:
            cfg_obj = json.loads(cfg_raw) if cfg_raw else {}
            cfg_obj["modelAliases"] = amap
            cur.execute("UPDATE harness_configs SET config = ? WHERE id = ?", (json.dumps(cfg_obj), hc_id))
            if spath:
                scfg = home / "storage" / spath / "config.yaml"
                if scfg.exists():
                    t = scfg.read_text()
                    block = "model_aliases:\n" + "".join(f"  {k}: {json.dumps(v)}\n" for k, v in amap.items())
                    nt, n = re.subn(r"(?m)^model_aliases:\n(?:  [a-z-]+:.*\n)+", block, t, count=1)
                    if n:
                        scfg.write_text(nt)
    lead_rows = cur.execute("SELECT id, applied_config FROM agents WHERE name = \x27acme-lead\x27").fetchall()
    for aid, acfg_raw in lead_rows:
        if acfg_raw:
            acfg = json.loads(acfg_raw)
            acfg["model"] = "claude-opus-5-5"
            if isinstance(acfg.get("inlineConfig"), dict):
                acfg["inlineConfig"]["model"] = "claude-opus-5-5"
            cur.execute("UPDATE agents SET applied_config = ? WHERE id = ?", (json.dumps(acfg), aid))
    conn.commit()
    conn.close()
print("updated model_aliases for claude, antigravity, gemini-cli")
PY
  sudo chown -R scion:scion /home/scion/.scion
'

echo "==> Hub-scoped GOOGLE_CLOUD_* env vars (agents inherit these)"
vm_ssh "sudo sqlite3 /home/scion/.scion/hub.db \
  \"SELECT key, value FROM env_vars WHERE key LIKE 'GOOGLE_CLOUD_%';\"" || \
  echo "(could not read env vars table; check in the Hub admin UI)"
