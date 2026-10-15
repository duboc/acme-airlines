#!/usr/bin/env bash
# Recreate and sync the Scion Hub configuration, harness model_aliases, the two
# Scion projects (${BUILD_PROJECT} on ${BUILD_BRANCH} and ${EVAL_PROJECT} on
# ${EVAL_BRANCH}), the 10 role templates (docs/scion/templates/), the 10 standing
# agents, and the two 15-minute heartbeat schedules using the native `scion` CLI
# (matching duboc/scion-experiment). Idempotent.
#
# Usage:
#   bash infra/setup-scion.sh                      # full setup
#   GITHUB_PAT=ghp_... bash infra/setup-scion.sh   # also sets/rotates GITHUB_TOKEN on both projects
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

AGY_VERSION="1.3.4"
AGENT_SA_EMAIL="${AGENT_SA}"
DEPLOYER_SA_EMAIL="${DEPLOYER_SA}"
BUILD_ROLES=(lead backend frontend reviewer deployer)
EVAL_ROLES=(eval-lead factory-analyst search-evaluator ui-craftsman eval-deployer)
ALL_ROLES=("${BUILD_ROLES[@]}" "${EVAL_ROLES[@]}")

echo "==> 1. Ensuring Vertex AI & deployer SA impersonation IAM + harness images (Claude Code + Antigravity ${AGY_VERSION})"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${VM_SA}" --role=roles/aiplatform.user \
  --condition=None --format=none

for role in roles/iam.serviceAccountTokenCreator roles/iam.serviceAccountUser; do
  gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA_EMAIL}" \
    --project="${PROJECT_ID}" --member="serviceAccount:${VM_SA}" --role="${role}" --format=none >/dev/null
done

vm_ssh "
  set -euo pipefail
  if ! sudo docker image inspect localhost/scion/scion-claude:latest >/dev/null 2>&1; then
    cd /opt/scion-source
    sudo docker build -t scion-claude:latest --build-arg BASE_IMAGE=scion-base:latest \
      -f harnesses/claude/Dockerfile harnesses/claude/
    sudo docker tag scion-claude:latest localhost/scion/scion-claude:latest
  fi
  cur_agy=\$(sudo docker run --rm --entrypoint agy scion-antigravity:latest --version 2>/dev/null || true)
  cur_user=\$(sudo docker inspect scion-antigravity:latest --format '{{.Config.User}}' 2>/dev/null || true)
  if [[ \"\${cur_agy}\" != \"${AGY_VERSION}\" ]]; then
    sudo docker build -t scion-antigravity:latest - <<'EOF'
FROM scion-antigravity:latest
USER root
RUN curl -fsSL -o /tmp/cli.tar.gz \"https://github.com/google-antigravity/antigravity-cli/releases/download/${AGY_VERSION}/agy_cli_linux_x64.tar.gz\" \\
  && tar -xzf /tmp/cli.tar.gz -C /tmp antigravity \\
  && mv /tmp/antigravity /usr/local/bin/agy \\
  && chmod +x /usr/local/bin/agy \\
  && rm /tmp/cli.tar.gz
USER root
EOF
    sudo docker tag scion-antigravity:latest localhost/scion/scion-antigravity:latest
  elif [[ \"\${cur_user}\" != \"root\" && -n \"\${cur_user}\" ]]; then
    sudo docker build -t scion-antigravity:latest - <<'EOF'
FROM scion-antigravity:latest
USER root
EOF
    sudo docker tag scion-antigravity:latest localhost/scion/scion-antigravity:latest
  fi
"

echo "==> 2. Enabling temporary operator federation on Hub (${VM_NAME})"
bash "${INFRA_DIR}/30-hub-admin-access.sh" enable
trap 'bash "${INFRA_DIR}/30-hub-admin-access.sh" disable' EXIT

echo "==> 3. Syncing harness model_aliases (Gemini 4 Argon, Flash 3.8, Opus 5.5, Sonnet 5.5)"
vm_ssh '
  set -euo pipefail
  sudo python3 - <<\PY
import json, pathlib, re, sqlite3

nl = chr(10)
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
    roots = [
        pathlib.Path("/home/scion/.scion/harness-configs") / hc_name,
        pathlib.Path("/opt/scion-source/harnesses") / hc_name,
    ] + list(pathlib.Path("/home/scion/.scion/workspace-storage").glob(f"hubs/*/harness-configs/global/{hc_name}"))
    for root in roots:
        cfg = root / "config.yaml"
        if cfg.exists():
            text = cfg.read_text()
            block = "model_aliases:" + nl + "".join(f"  {k}: {json.dumps(v)}" + nl for k, v in aliases.items())
            new_text, n = re.subn(r"(?m)^model_aliases:\r?\n(?:  [a-z-]+:.*\r?\n)+", lambda _: block, text, count=1)
            if n:
                cfg.write_text(new_text)

con = sqlite3.connect("/home/scion/.scion/hub.db")
cur = con.cursor()
for hc_name, aliases in aliases_map.items():
    cur.execute("SELECT id, config FROM harness_configs WHERE slug = ?", (hc_name,))
    for hid, cfg_raw in cur.fetchall():
        cfg = json.loads(cfg_raw or "{}")
        cfg["modelAliases"] = aliases
        cur.execute("UPDATE harness_configs SET config = ? WHERE id = ?", (json.dumps(cfg), hid))
con.commit()
PY
  sudo chown -R scion:scion /home/scion/.scion/harness-configs /home/scion/.scion/workspace-storage 2>/dev/null || true
  mkdir -p ~/.scion/harness-configs
  sudo cp -r /home/scion/.scion/harness-configs/{claude,antigravity,gemini-cli} ~/.scion/harness-configs/
  sudo chown -R "$(id -un):$(id -gn)" ~/.scion
'

if [[ "${SKIP_SYNC:-0}" != "1" ]]; then
  for hc in claude antigravity gemini-cli; do
    hub_cli --global harness-config sync "${hc}"
  done
  # Re-apply modelAliases in hub.db and workspace-storage after harness-config sync
  vm_ssh '
    set -euo pipefail
    sudo python3 - <<\PY
import json, pathlib, re, sqlite3
nl = chr(10)
aliases_map = {
    "claude": {"small": "claude-haiku-5-5", "medium": "claude-sonnet-5-5", "large": "claude-opus-5-5", "extra-large": "claude-opus-5-5"},
    "antigravity": {"small": "Gemini 3.1 Flash Lite", "medium": "Gemini 3.8 Flash (Medium)", "large": "Gemini 4 Argon (High)", "extra-large": "Gemini 4 Argon (High)"},
    "gemini-cli": {"small": "gemini-3.5-flash-lite", "medium": "gemini-3.8-flash", "large": "gemini-4-argon", "extra-large": "gemini-4-argon"},
}
for hc_name, aliases in aliases_map.items():
    for cfg in pathlib.Path("/home/scion/.scion/workspace-storage").glob(f"hubs/*/harness-configs/global/{hc_name}/config.yaml"):
        text = cfg.read_text()
        block = "model_aliases:" + nl + "".join(f"  {k}: {json.dumps(v)}" + nl for k, v in aliases.items())
        new_text, n = re.subn(r"(?m)^model_aliases:\r?\n(?:  [a-z-]+:.*\r?\n)+", lambda _: block, text, count=1)
        if n:
            cfg.write_text(new_text)
con = sqlite3.connect("/home/scion/.scion/hub.db")
cur = con.cursor()
for hc_name, aliases in aliases_map.items():
    cur.execute("SELECT id, config FROM harness_configs WHERE slug = ?", (hc_name,))
    for hid, cfg_raw in cur.fetchall():
        cfg = json.loads(cfg_raw or "{}")
        cfg["modelAliases"] = aliases
        cur.execute("UPDATE harness_configs SET config = ? WHERE id = ?", (json.dumps(cfg), hid))
con.commit()
PY
    sudo chown -R scion:scion /home/scion/.scion/workspace-storage 2>/dev/null || true
  '

  echo "==> 4. Rendering, importing, and syncing the 10 templates from docs/scion/templates/ via 'scion templates import' + 'sync'"
  LOCAL_RENDER_DIR="$(mktemp -d "${TMPDIR:-/tmp}/scion-rendered.XXXXXX")"
  python3 "${INFRA_DIR}/render-templates.py" --out "${LOCAL_RENDER_DIR}"
  vm_ssh "rm -rf /tmp/scion-templates ~/.scion/templates && mkdir -p /tmp/scion-templates ~/.scion/templates"
  gcloud compute scp --recurse "${LOCAL_RENDER_DIR}/"* "${VM_NAME}:/tmp/scion-templates/" \
    --project="${PROJECT_ID}" --zone="$(resolve_zone)" --tunnel-through-iap --quiet
  rm -rf "${LOCAL_RENDER_DIR}"

  hub_cli --global templates import --all --force /tmp/scion-templates
  hub_cli --global templates sync --all
  # Update existing agent scion-agent.json files on disk + hub.db to remove any legacy max_duration/max_turns/max_model_calls limits and sync instructions
  vm_ssh '
    set -euo pipefail
    sudo python3 - <<\PY
import json, pathlib, shutil, sqlite3

model_by_role = {
    "lead": "claude-opus-5-5",
    "backend": "claude-sonnet-5-5",
    "deployer": "claude-sonnet-5-5",
    "frontend": "Gemini 4 Argon (High)",
    "reviewer": "Gemini 4 Argon (High)",
    "eval-lead": "Gemini 4 Argon (High)",
    "factory-analyst": "Gemini 4 Argon (High)",
    "search-evaluator": "Gemini 4 Argon (High)",
    "ui-craftsman": "Gemini 4 Argon (High)",
    "eval-deployer": "Gemini 4 Argon (High)",
}

for jf in pathlib.Path("/home/scion/.scion/project-configs").glob("*/.scion/agents/*/scion-agent.json"):
    role = jf.parent.name
    if role not in model_by_role:
        continue
    data = json.loads(jf.read_text())
    for k in ("max_duration", "max_turns", "max_model_calls"):
        data.pop(k, None)
    data["model"] = model_by_role[role]
    jf.write_text(json.dumps(data, indent=2) + "\n")
    src_tpl = pathlib.Path("/tmp/scion-templates") / role
    if src_tpl.exists():
        for doc in ("agents.md", "system-prompt.md"):
            if (src_tpl / doc).exists():
                for dst in [jf.parent / doc, jf.parent / "home" / ".scion" / doc]:
                    if dst.parent.exists():
                        shutil.copy2(src_tpl / doc, dst)

con = sqlite3.connect("/home/scion/.scion/hub.db")
cur = con.cursor()
cur.execute("SELECT id, name, applied_config FROM agents WHERE deleted_at IS NULL")
for aid, name, ac_raw in cur.fetchall():
    ac = json.loads(ac_raw or "{}")
    ic = ac.get("inlineConfig") or {}
    for k in ("max_duration", "max_turns", "max_model_calls", "maxDuration", "maxTurns", "maxModelCalls"):
        ac.pop(k, None)
        ic.pop(k, None)
    ac["inlineConfig"] = ic
    cur.execute(
        "UPDATE agents SET applied_config = ?, current_turns = 0, current_model_calls = 0 WHERE id = ?",
        (json.dumps(ac), aid),
    )
con.commit()
PY
    sudo chown -R scion:scion /home/scion/.scion/project-configs 2>/dev/null || true
    rm -rf /tmp/scion-templates
  '

  # Remove obsolete templates if still registered
  for obs in coordinator eng-manager architect developer code-reviewer test-engineer \
             acme-lead acme-eng-manager acme-architect acme-backend acme-frontend \
             acme-reviewer acme-security-auditor acme-qa-tester acme-investigator \
             acme-researcher acme-doc-writer acme-release-notes; do
    hub_cli --global templates delete "${obs}" --force >/dev/null 2>&1 || true
  done
else
  echo "==> 4. Skipping harness & template upload sync (SKIP_SYNC=1)"
fi

echo "==> 5. Configuring both Hub projects (${BUILD_PROJECT} & ${EVAL_PROJECT}), shared workspaces, secrets, and service accounts"
hub_cli hub env set --scope=hub --always "GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
hub_cli hub env set --scope=hub --always "GOOGLE_CLOUD_LOCATION=${VERTEX_LOCATION:-global}"

VM_SA_EMAIL="${VM_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
for sa_email in "${AGENT_SA_EMAIL}" "${DEPLOYER_SA_EMAIL}"; do
  gcloud iam service-accounts add-iam-policy-binding "${sa_email}" --project="${PROJECT_ID}" \
    --member="serviceAccount:${VM_SA_EMAIL}" --role="roles/iam.serviceAccountTokenCreator" --format=none >/dev/null 2>&1 || true
  gcloud iam service-accounts add-iam-policy-binding "${sa_email}" --project="${PROJECT_ID}" \
    --member="user:${ADMIN_EMAIL}" --role="roles/iam.serviceAccountUser" --format=none >/dev/null 2>&1 || true
done

for pair in "${BUILD_PROJECT}:${BUILD_BRANCH}" "${EVAL_PROJECT}:${EVAL_BRANCH}"; do
  slug="${pair%%:*}"; branch="${pair#*:}"
  exists="$(vm_ssh "sudo -u scion sqlite3 /home/scion/.scion/hub.db \"SELECT id FROM projects WHERE slug='${slug}';\"")"
  if [[ -z "${exists}" ]]; then
    echo "    Creating shared-workspace project ${slug} (branch ${branch})..."
    hub_cli hub projects create "https://github.com/${GITHUB_REPO}.git" \
      --slug "${slug}" --name "${slug}" --branch "${branch}" --workspace-mode shared
  fi
done

ensure_global_sa() {
  local email="$1" label="$2" id
  id="$(hub_cli --global service-accounts list --json 2>/dev/null \
    | python3 -c "import json,sys; raw=sys.stdin.read(); idx=raw.find('['); idx=raw.find('{') if idx<0 else idx; d=json.loads(raw[idx:]) if idx>=0 else []; items=d if isinstance(d,list) else d.get('items',[]); print(next((x['id'] for x in items if x.get('email')=='${email}'),''))" || true)"
  if [[ -z "${id}" ]]; then
    hub_cli --global service-accounts add "${email}" --gcp-project "${PROJECT_ID}" --name "${label}" >/dev/null 2>&1 || true
    id="$(hub_cli --global service-accounts list --json 2>/dev/null \
      | python3 -c "import json,sys; raw=sys.stdin.read(); idx=raw.find('['); idx=raw.find('{') if idx<0 else idx; d=json.loads(raw[idx:]) if idx>=0 else []; items=d if isinstance(d,list) else d.get('items',[]); print(next((x['id'] for x in items if x.get('email')=='${email}'),''))" || true)"
  fi
  [[ -n "${id}" ]] && hub_cli --global service-accounts verify "${id}" >/dev/null 2>&1 || true
}

ensure_global_sa "${AGENT_SA_EMAIL}" "ACME Agent SA"
ensure_global_sa "${DEPLOYER_SA_EMAIL}" "ACME Deployer SA"

# Ensure both projects have project-scoped verified SAs, shared workspace mode, scratchpad shared_dir, and GITHUB_TOKEN (injection_mode='always')
SA_IDS_RAW="$(vm_ssh "
  set -euo pipefail
  sudo -u scion python3 - '${BUILD_PROJECT}' '${BUILD_BRANCH}' '${EVAL_PROJECT}' '${EVAL_BRANCH}' '${GITHUB_REPO}' '${AGENT_SA_EMAIL}' '${DEPLOYER_SA_EMAIL}' <<'PY'
import json, sqlite3, sys, uuid, datetime

build_slug, build_branch, eval_slug, eval_branch, repo, agent_sa_email, deployer_sa_email = sys.argv[1:8]
con = sqlite3.connect('/home/scion/.scion/hub.db')
cur = con.cursor()
now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

cur.execute('SELECT id FROM projects WHERE slug = ?', (build_slug,))
build_id = cur.fetchone()[0]
cur.execute('SELECT id FROM projects WHERE slug = ?', (eval_slug,))
eval_id = cur.fetchone()[0]

cols = [c[1] for c in cur.execute('PRAGMA table_info(gcp_service_accounts)').fetchall()]

def ensure_project_sa_row(pid, email):
    cur.execute(\"SELECT id FROM gcp_service_accounts WHERE scope='project' AND scope_id=? AND email=?\", (pid, email))
    r = cur.fetchone()
    if r:
        cur.execute(\"UPDATE gcp_service_accounts SET verified=1, verification_status='verified' WHERE id=?\", (r[0],))
        return r[0]
    cur.execute(f\"SELECT {','.join(cols)} FROM gcp_service_accounts WHERE email=? ORDER BY verified DESC LIMIT 1\", (email,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f'global SA {email} not found in gcp_service_accounts')
    src = dict(zip(cols, row))
    new_id = str(uuid.uuid4())
    src['id'] = new_id
    src['scope'] = 'project'
    src['scope_id'] = pid
    src['verified'] = 1
    if 'verification_status' in src:
        src['verification_status'] = 'verified'
    placeholders = ','.join(['?'] * len(cols))
    cur.execute(f\"INSERT INTO gcp_service_accounts ({','.join(cols)}) VALUES ({placeholders})\", [src[c] for c in cols])
    return new_id

build_agent_sa_id = ensure_project_sa_row(build_id, agent_sa_email)
build_deployer_sa_id = ensure_project_sa_row(build_id, deployer_sa_email)
eval_agent_sa_id = ensure_project_sa_row(eval_id, agent_sa_email)
eval_deployer_sa_id = ensure_project_sa_row(eval_id, deployer_sa_email)

for pid, pslug, branch, sa_id in [
    (build_id, build_slug, build_branch, build_agent_sa_id),
    (eval_id, eval_slug, eval_branch, eval_agent_sa_id),
]:
    cur.execute('SELECT labels, annotations FROM projects WHERE id = ?', (pid,))
    lraw, araw = cur.fetchone()
    labels = json.loads(lraw or '{}')
    labels['scion.dev/clone-url'] = f'https://github.com/{repo}.git'
    labels['scion.dev/source-url'] = f'https://github.com/{repo}.git'
    labels['scion.dev/default-branch'] = branch
    labels['scion.dev/workspace-mode'] = 'shared'
    ann = json.loads(araw or '{}')
    ann['scion.io/default-gcp-identity-mode'] = 'assign'
    ann['scion.io/default-gcp-identity-service-account-id'] = sa_id
    ann['scion.io/default-gcp-identity-sa-id'] = sa_id
    ann['scion.io/default-gcp-identity-sa-email'] = agent_sa_email
    shared_dirs = json.dumps([{'name': 'scratchpad'}])
    cur.execute(
        'UPDATE projects SET labels = ?, annotations = ?, shared_dirs = ?, updated = ? WHERE id = ?',
        (json.dumps(labels), json.dumps(ann), shared_dirs, now, pid),
    )

# Copy GITHUB_TOKEN secret from build_project to eval_project if missing, and set injection_mode='always' on both
cur.execute(\"SELECT encrypted_value, secret_type, created_by FROM secrets WHERE scope='project' AND scope_id=? AND key='GITHUB_TOKEN'\", (build_id,))
srow = cur.fetchone()
if srow:
    enc_val, stype, cby = srow
    cur.execute(\"UPDATE secrets SET injection_mode='always', updated=? WHERE scope='project' AND scope_id=? AND key='GITHUB_TOKEN'\", (now, build_id))
    cur.execute(\"SELECT id FROM secrets WHERE scope='project' AND scope_id=? AND key='GITHUB_TOKEN'\", (eval_id,))
    if not cur.fetchone():
        cur.execute('''
            INSERT INTO secrets (id, key, encrypted_value, scope, scope_id, secret_type, injection_mode, version, created, updated, created_by, updated_by)
            VALUES (?, 'GITHUB_TOKEN', ?, 'project', ?, ?, 'always', 1, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), enc_val, eval_id, stype, now, now, cby, cby))
    else:
        cur.execute(\"UPDATE secrets SET encrypted_value=?, injection_mode='always', updated=? WHERE scope='project' AND scope_id=? AND key='GITHUB_TOKEN'\", (enc_val, now, eval_id))

con.commit()
print(f'{build_agent_sa_id}|{build_deployer_sa_id}|{eval_agent_sa_id}|{eval_deployer_sa_id}')
PY
")"
IFS='|' read -r BUILD_AGENT_SA_ID BUILD_DEPLOYER_SA_ID EVAL_AGENT_SA_ID EVAL_DEPLOYER_SA_ID <<<"$(tail -1 <<<"${SA_IDS_RAW}")"
echo "    Verified project SAs: build(agent=${BUILD_AGENT_SA_ID}, deployer=${BUILD_DEPLOYER_SA_ID}) eval(agent=${EVAL_AGENT_SA_ID}, deployer=${EVAL_DEPLOYER_SA_ID})"

if [[ -n "${GITHUB_PAT:-}" ]]; then
  for p in "${BUILD_PROJECT}" "${EVAL_PROJECT}"; do
    hub_cli hub secret set "--project=${p}" GITHUB_TOKEN "${GITHUB_PAT}"
  done
fi

# Ensure both project workspaces on the VM are clean git checkouts on BUILD_BRANCH and EVAL_BRANCH
vm_ssh "
  set -euo pipefail
  sudo -u scion bash -c '
    for pair in \"${BUILD_PROJECT}:${BUILD_BRANCH}\" \"${EVAL_PROJECT}:${EVAL_BRANCH}\"; do
      slug=\"\${pair%%:*}\"; branch=\"\${pair#*:}\"
      dir=\"/home/scion/.scion/projects/\${slug}\"
      mkdir -p \"\${dir}\"
      if [[ ! -d \"\${dir}/.git\" ]]; then
        git -C \"\${dir}\" init -q -b \"\${branch}\"
        git -C \"\${dir}\" remote add origin \"https://github.com/${GITHUB_REPO}.git\" || true
      fi
      git -C \"\${dir}\" remote set-url origin \"https://github.com/${GITHUB_REPO}.git\"
      git -C \"\${dir}\" fetch -q origin \"\${branch}\" || true
      if git -C \"\${dir}\" show-ref --verify --quiet \"refs/remotes/origin/\${branch}\"; then
        git -C \"\${dir}\" checkout -B \"\${branch}\" \"origin/\${branch}\" -f -q
      fi
      git -C \"\${dir}\" config user.name \"Scion Factory Agent\"
      git -C \"\${dir}\" config user.email \"${AGENT_SA_EMAIL}\"
      git -C \"\${dir}\" config credential.helper \"!f() { echo username=x-access-token; echo password=\\\${GITHUB_TOKEN}; }; f\"
      mkdir -p \"\${dir}/.git/info\"
      touch \"\${dir}/.git/info/exclude\"
      for pat in \".scion\" \".scion/\" \".agents/\" \"scratchpad/\" \"*.env\" \".env*\"; do
        grep -qxF \"\${pat}\" \"\${dir}/.git/info/exclude\" || echo \"\${pat}\" >> \"\${dir}/.git/info/exclude\"
      done
    done
  '
"

echo "==> 6. Starting the 10 standing agents across ${BUILD_PROJECT} and ${EVAL_PROJECT} via 'scion start'"
gcloud auth print-identity-token 2>/dev/null | vm_ssh "
  set -euo pipefail
  IFS= read -r SCION_HUB_TOKEN; export SCION_HUB_TOKEN
  export SCION_HUB_ENDPOINT=http://localhost:8080
  mkdir -p ~/acme-cli && cd ~/acme-cli

  scion --project '${BUILD_PROJECT}' delete acme-lead --force >/dev/null 2>&1 || true
  sudo docker ps -a --format '{{.Names}}' | grep -E '^${BUILD_PROJECT}--acme-' | xargs -r sudo docker rm -f >/dev/null 2>&1 || true

  ensure_agent() {
    local proj=\"\$1\" name=\"\$2\" role_flag=\"\$3\" sa_id=\"\$4\" wait_flag=\"\$5\" prompt=\"\$6\"
    local phase
    phase=\$(sudo -u scion sqlite3 /home/scion/.scion/hub.db \"SELECT a.phase FROM agents a JOIN projects p ON a.project_id = p.id WHERE p.slug='\${proj}' AND a.name='\${name}' AND a.deleted_at IS NULL LIMIT 1;\" | tr -d '[:space:]')
    if [[ \"\${phase}\" == \"created\" || \"\${phase}\" == \"error\" ]]; then
      scion --project \"\${proj}\" delete \"\${name}\" --force >/dev/null 2>&1 || true
      phase=\"\"
    fi
    if [[ -z \"\${phase}\" ]]; then
      local -a extra_flags=(\"--service-account\" \"\${sa_id}\")
      [[ \"\${role_flag}\" == \"full\" ]] && extra_flags+=(\"--role\" \"full\")
      [[ \"\${wait_flag}\" == \"nowait\" ]] && extra_flags+=(\"--no-wait\")
      scion --project \"\${proj}\" start \"\${name}\" --type \"\${name}\" \"\${extra_flags[@]}\" \"\${prompt}\"
    elif [[ \"\${phase}\" == \"stopped\" ]]; then
      echo \"    resuming stopped standing agent \${proj}/\${name}...\"
      scion --project \"\${proj}\" resume \"\${name}\" \"\${prompt}\" || scion --project \"\${proj}\" start \"\${name}\" \"\${prompt}\" || true
    else
      echo \"    agent \${proj}/\${name} already exists (phase=\${phase})\"
    fi
  }

  ensure_agent '${BUILD_PROJECT}' 'lead'      'full' '${BUILD_AGENT_SA_ID}'    'wait'   'Read your instructions, then run your wake-up steps.'
  ensure_agent '${BUILD_PROJECT}' 'backend'   'base' '${BUILD_AGENT_SA_ID}'    'nowait' 'Stand by for briefs from lead. End your turn with sciontool status blocked \"waiting: brief from lead\".'
  ensure_agent '${BUILD_PROJECT}' 'frontend'  'base' '${BUILD_AGENT_SA_ID}'    'nowait' 'Stand by for briefs from lead. End your turn with sciontool status blocked \"waiting: brief from lead\".'
  ensure_agent '${BUILD_PROJECT}' 'reviewer'  'base' '${BUILD_AGENT_SA_ID}'    'nowait' 'Stand by for review requests from lead. End your turn with sciontool status blocked \"waiting: review request from lead\".'
  ensure_agent '${BUILD_PROJECT}' 'deployer'  'base' '${BUILD_DEPLOYER_SA_ID}' 'nowait' 'Stand by for deploy requests from lead. End your turn with sciontool status blocked \"waiting: deploy request from lead\".'

  ensure_agent '${EVAL_PROJECT}'  'eval-lead'        'full' '${EVAL_AGENT_SA_ID}'    'wait'   'Read your instructions, then run your wake-up steps.'
  ensure_agent '${EVAL_PROJECT}'  'factory-analyst'  'base' '${EVAL_AGENT_SA_ID}'    'nowait' 'Stand by for briefs from eval-lead. End your turn with sciontool status blocked \"waiting: brief from eval-lead\".'
  ensure_agent '${EVAL_PROJECT}'  'search-evaluator' 'base' '${EVAL_AGENT_SA_ID}'    'nowait' 'Stand by for briefs from eval-lead. End your turn with sciontool status blocked \"waiting: brief from eval-lead\".'
  ensure_agent '${EVAL_PROJECT}'  'ui-craftsman'     'base' '${EVAL_AGENT_SA_ID}'    'nowait' 'Stand by for briefs from eval-lead. End your turn with sciontool status blocked \"waiting: brief from eval-lead\".'
  ensure_agent '${EVAL_PROJECT}'  'eval-deployer'    'base' '${EVAL_DEPLOYER_SA_ID}' 'nowait' 'Stand by for deploy requests from eval-lead. End your turn with sciontool status blocked \"waiting: deploy request from eval-lead\".'
"


echo "==> 7. Installing the two 15-minute heartbeat schedules (${BUILD_PROJECT}-lead-heartbeat & ${EVAL_PROJECT}-lead-heartbeat)"
bash "${INFRA_DIR}/60-factory.sh" tick-install
echo "==> Done: 2 projects (${BUILD_PROJECT}, ${EVAL_PROJECT}), 10 templates, 10 agents, and 2 15-min heartbeats active."
