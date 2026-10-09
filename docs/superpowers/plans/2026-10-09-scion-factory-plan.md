# Scion Software Factory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a Scion Hub + broker on one GCE VM in a new GCP project, wired to Vertex AI (global) and GitHub, proven by two agent-opened PRs, with every step documented in this repo.

**Architecture:** Bootstrap project with gcloud, then run Scion's supported `scripts/single-node-vm/deploy.sh --config` headlessly (VM + Cloud Run IAP proxy), then post-steps for Vertex IAM, the Claude image, the Hub project, and smoke tests. A single `infra/verify.sh` is the test harness: each task adds a check that fails first and passes after.

**Tech Stack:** gcloud, bash, jq, gh, Scion `nightly-20261009` release, Docker on Ubuntu 22.04, Vertex AI (`locations/global`).

**Spec:** `docs/superpowers/specs/2026-10-09-scion-factory-design.md`

## Global Constraints

- Project ID `acme-airlines-factory` (suffix only if taken), folder `543080121395` (`dubofolder`), billing `01820C-6329B4-8A7409`.
- Region `us-central1`; hub name `acme`; machine `medium`; disk `500`; images `build`; update policy `notify`; admin `admin@duboc.altostrat.com`.
- All Vertex AI calls use `locations/global` only — no regional fallback.
- Scion source checkout for deploy: `/Users/duboc/local/projects/scion` (local) at release tag `nightly-20261009`; VM source at `/opt/scion-source`.
- No secrets in git, chat, or shell argv. `GITHUB_TOKEN` is set by the human via the Hub web UI.
- Every command actually run is appended to `docs/setup/01-runbook.md` with its purpose; every problem hit goes into `docs/setup/03-troubleshooting.md`.
- Do not create/delete agents in the Scion source repo's own `.scion` project.

## Review Focus

1. Project ID taken globally → script must detect and use the suffixed ID everywhere (config, docs); verify.sh reads ID from `infra/deploy-config.json`, never hard-codes it.
2. Argolis org policy blocks a step (no default network, domain-restricted sharing) → bootstrap creates `default` auto network idempotently; verify checks network+subnet exist.
3. Re-running any infra script → must be idempotent (exists-checks before create); Task 2/4 run their script twice and verify still passes.
4. Claude harness resolves region to something other than `global` → Task 4 inspects a started agent's env for `CLOUD_ML_REGION=global`.
5. PAT missing/insufficient scope → smoke agent fails at clone/push; Task 5 verify checks secret presence (name only) before Task 6 starts.

---

### Task 1: Repository scaffold and verify harness

**Files:**
- Create: `README.md`, `AGENTS.md`, `.gitignore`, `app/.gitkeep`
- Create: `docs/setup/00-overview.md`, `01-runbook.md`, `02-operations.md`, `03-troubleshooting.md`, `04-teardown.md` (headings only except overview)
- Create: `infra/lib.sh`, `infra/verify.sh`, `infra/deploy-config.json`

**Interfaces:**
- Produces: `infra/lib.sh` exporting `PROJECT_ID`, `REGION`, `HUB_NAME`, `ZONE` (read via `jq` from `infra/deploy-config.json`; `ZONE` = zone of the existing VM via `gcloud compute instances list --filter=name=$VM_NAME`, falling back to `${REGION}-b` as deploy.sh does), `VM_NAME=scion-hub-${HUB_NAME}`, `VM_SA=scion-hub-${HUB_NAME}@${PROJECT_ID}.iam.gserviceaccount.com`, `PROXY_SVC=scion-hub-${HUB_NAME}-iap-proxy`, and `vm_ssh "<cmd>"` (wraps `gcloud compute ssh --tunnel-through-iap`).
- Produces: `infra/verify.sh <check>` with checks `project|hub|vertex|claude-image|hubproject|smoke|all`; prints `PASS <check>` / `FAIL <check>: <reason>`, exits non-zero on fail.

- [ ] **Step 1:** Write `infra/deploy-config.json` with exactly the Global Constraints values (`project_id: "acme-airlines-factory"`, `chat_plugins: []`, `container_images.source: "build"`, `release_channel: "nightly"`).
- [ ] **Step 2:** Write `lib.sh` and `verify.sh` with all checks defined (bodies per later tasks' "verify" steps; each check stubbed to FAIL "not implemented" until its task).
- [ ] **Step 3:** Run `bash infra/verify.sh project` → Expected: `FAIL project` (project doesn't exist yet). Run `shellcheck infra/*.sh` → clean.
- [ ] **Step 4:** Write README (purpose, layout, links to docs/setup), AGENTS.md (repo rules: no secrets, app lives in `app/`, PRs only, docs updates with changes), `.gitignore` (`.env*`, `.envrc`, `*.key`, `*.pem`, `node_modules/`, `.DS_Store`).
- [ ] **Step 5:** Commit and push `main`: `git push -u origin main`. Verify: `gh repo view duboc/acme-airlines --json defaultBranchRef` → `main`.

### Task 2: GCP project bootstrap

**Files:**
- Create: `infra/00-bootstrap-project.sh`
- Modify: `infra/verify.sh` (`project` check), `docs/setup/01-runbook.md`, `docs/setup/00-overview.md` (org policies found)

**Interfaces:**
- Consumes: `lib.sh` vars.
- Produces: project with billing, APIs, `default` network/subnet in `us-central1`.

- [ ] **Step 1: verify `project` check** asserts: `gcloud projects describe $PROJECT_ID` succeeds; `gcloud billing projects describe` shows `billingEnabled: true`; `gcloud services list --enabled` contains `compute run iap cloudbuild artifactregistry aiplatform iamcredentials logging monitoring`; `gcloud compute networks subnets describe default --region us-central1` succeeds.
- [ ] **Step 2:** Run `bash infra/verify.sh project` → FAIL.
- [ ] **Step 3:** Implement `00-bootstrap-project.sh`: create project under folder if absent (on ID-taken error, append `-$(openssl rand -hex 2)` and write it back into `deploy-config.json`), link billing, enable APIs, `gcloud org-policies list --project` to stdout, create `default` auto-mode network if absent (custom subnet `10.128.0.0/20` only if auto-mode is rejected), and `gcloud config configurations create acme-factory` with project set (don't change user's default config).
- [ ] **Step 4:** Run script; run again (idempotency); `bash infra/verify.sh project` → PASS.
- [ ] **Step 5:** Record commands, final project ID/number, and org policies in runbook/overview. Commit `feat(infra): bootstrap acme-airlines-factory project`.

### Task 3: Deploy the Hub

**Files:**
- Create: `infra/10-deploy-hub.sh`
- Modify: `infra/verify.sh` (`hub` check), runbook, overview (resource inventory + proxy URL)

**Interfaces:**
- Consumes: `deploy-config.json`, Scion checkout path.
- Produces: VM `scion-hub-acme`, Cloud Run `scion-hub-acme-iap-proxy`, VM SA; images `localhost/scion/{core-base,scion-base,scion-antigravity}:latest`.

- [ ] **Step 1: verify `hub` check** asserts: VM status `RUNNING`; `vm_ssh "curl -sf http://localhost:8080/healthz"` succeeds (same path deploy.sh uses); `vm_ssh "systemctl is-active scion-hub"` = `active`; Cloud Run service URL resolves and unauthenticated `curl -s -o /dev/null -w '%{http_code}'` returns `302` or `401/403` (IAP enforcing); `vm_ssh "sudo docker image inspect localhost/scion/scion-antigravity:latest"` succeeds.
- [ ] **Step 2:** `bash infra/verify.sh hub` → FAIL.
- [ ] **Step 3:** Implement `10-deploy-hub.sh`: `cd` to Scion checkout, `CLOUDSDK_ACTIVE_CONFIG_NAME=acme-factory ./scripts/single-node-vm/deploy.sh --version nightly-20261009 --config <abs path to infra/deploy-config.json> 2>&1 | tee` into a gitignored `infra/logs/` file.
- [ ] **Step 4:** Run it (long: 30–45 min image build; run in background, notify on completion). On failure: consult deploy.sh, fix, re-run, log in troubleshooting.
- [ ] **Step 5:** `bash infra/verify.sh hub` → PASS. Human opens proxy URL and confirms login as admin.
- [ ] **Step 6:** Record proxy URL, Scion version, created resources in overview/runbook. Commit `feat(infra): deploy scion hub via single-node-vm`.

### Task 4: Vertex AI (global) access and Claude image

**Files:**
- Create: `infra/20-model-access.sh`
- Modify: `infra/verify.sh` (`vertex`, `claude-image` checks), runbook, troubleshooting

**Interfaces:**
- Consumes: `VM_SA`, `vm_ssh`, `/opt/scion-source` on VM.
- Produces: `roles/aiplatform.user` on `VM_SA`; `localhost/scion/scion-claude:latest`; hub env vars ensuring `global`.

- [ ] **Step 1: verify `vertex` check** runs on the VM with metadata token: POST `https://aiplatform.googleapis.com/v1/projects/$PROJECT_ID/locations/global/publishers/google/models/gemini-2.5-flash:generateContent` → HTTP 200; POST `.../locations/global/publishers/anthropic/models/<claude model>:rawPredict` (model ID = the one enabled in Model Garden, recorded in config as `claude_model`) → HTTP 200. **`claude-image`** check: `docker image inspect localhost/scion/scion-claude:latest`.
- [ ] **Step 2:** Run both checks → FAIL.
- [ ] **Step 3:** Implement `20-model-access.sh`: idempotent `add-iam-policy-binding` for `roles/aiplatform.user`; on VM, in `/opt/scion-source`, `docker build -t scion-claude:latest --build-arg BASE_IMAGE=scion-base:latest -f harnesses/claude/Dockerfile harnesses/claude/` then tag `localhost/scion/scion-claude:latest`; ensure hub env vars `GOOGLE_CLOUD_LOCATION=global` and `GOOGLE_CLOUD_REGION=global` exist (via `scion hub env` CLI on VM, or admin UI — record which).
- [ ] **Step 4 (human):** Enable the Claude model(s) in Vertex Model Garden for the project; tell the agent the model ID(s).
- [ ] **Step 5:** Run script; `verify.sh vertex` and `claude-image` → PASS.
- [ ] **Step 6:** Commit `feat(infra): vertex global access and claude image`.

### Task 5: Register acme-airlines on the Hub

**Files:**
- Modify: `infra/verify.sh` (`hubproject` check), runbook, `docs/setup/02-operations.md` (CLI access)

**Interfaces:**
- Produces: Hub project `acme-airlines` from `https://github.com/duboc/acme-airlines.git` with secret `GITHUB_TOKEN`.

- [ ] **Step 1: verify `hubproject` check**: on the VM (as `scion` user, hub at localhost) `scion hub project list` contains `acme-airlines`; `scion hub secret list --project acme-airlines` lists `GITHUB_TOKEN` (name only).
- [ ] **Step 2:** → FAIL.
- [ ] **Step 3:** Create the project via Hub web UI or `scion hub project create https://github.com/duboc/acme-airlines.git` (record which path worked and how CLI authenticates).
- [ ] **Step 4 (human):** Create fine-grained PAT (repo `duboc/acme-airlines`; Contents RW, Pull requests RW, Metadata R; 90-day expiry) and add it as project secret `GITHUB_TOKEN` in the Hub UI.
- [ ] **Step 5:** → PASS. Commit `docs: register acme-airlines on hub`.

### Task 6: Smoke test both harnesses

**Files:**
- Create: `docs/factory/smoke-tests.md` (header only, on `main`)
- Modify: `infra/verify.sh` (`smoke` check), runbook, troubleshooting

- [ ] **Step 1: verify `smoke` check**: `gh pr list -R duboc/acme-airlines --state all --search "smoke" --json title` contains one PR from each of `smoke-gemini`, `smoke-claude`.
- [ ] **Step 2:** → FAIL.
- [ ] **Step 3:** Start `smoke-gemini` (harness `antigravity`) and `smoke-claude` (harness `claude`) in project `acme-airlines` with task: "Create branch `smoke/<agent-name>`, append a line `- <harness> — <UTC date> — ok` to `docs/factory/smoke-tests.md`, commit, push, and open a PR titled `smoke: <agent-name>`." While `smoke-claude` runs, confirm its env has `CLOUD_ML_REGION=global` and `CLAUDE_CODE_USE_VERTEX=1`.
- [ ] **Step 4:** → PASS. Merge both PRs (human decides), delete smoke agents.
- [ ] **Step 5:** Commit `docs: smoke test results`.

### Task 7: Operations, teardown, ADRs, final review

**Files:**
- Create: `infra/90-teardown.sh`, `docs/decisions/0001-…0008-*.md` (one per spec decision D1–D8; context/decision/consequences, ≤20 lines each)
- Modify: `docs/setup/02-operations.md`, `04-teardown.md`, `00-overview.md` (cost), `README.md`

- [ ] **Step 1:** `02-operations.md`: IAP browser access + granting users, SSH, logs (`journalctl -u scion-hub`), stop/start VM, upgrades (notify policy), cost (~$550–600/mo).
- [ ] **Step 2:** `90-teardown.sh`: `deploy.sh --delete` with same config, then optional `gcloud projects delete` behind an explicit `--delete-project` flag + confirmation. Do **not** run it. `shellcheck` clean.
- [ ] **Step 3:** ADRs D1–D8.
- [ ] **Step 4:** `bash infra/verify.sh all` → all PASS. Read runbook top-to-bottom against shell history for gaps; `rg -i "github_pat_|token=|secret=" .` returns nothing.
- [ ] **Step 5:** `git diff origin/main` self-review; commit `docs: operations, teardown, ADRs`; push.
