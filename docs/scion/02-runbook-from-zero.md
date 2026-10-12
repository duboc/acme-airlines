# 2. Runbook: build a factory from zero

Each step is one idempotent script under [`infra/`](../../infra/), followed by
a `verify.sh` check. Re-running any step is safe.
[`scripts/bootstrap.sh`](scripts/bootstrap.sh) runs them in this order and
pauses at the manual steps (marked 🖐).

**Operator prerequisites:**
- `gcloud` (logged in, with Owner on the folder or project and Billing User
  on the billing account)
- `gh` (logged in, admin on the repo)
- `jq`, `uv`, `git`, `bash` 4+

All scripts use a dedicated gcloud configuration (`acme-factory`), so your
default config is never changed.

| # | Step | Command | Check | Time |
|---|---|---|---|---|
| 0 | Parameters | edit `infra/factory.env` and `infra/deploy-config.json` | — | 5 min |
| 1 | GCP project, billing, APIs, `default` VPC | `infra/00-bootstrap-project.sh` | `verify.sh project` | 2 min |
| 2 | Hub VM + IAP proxy (Scion `deploy.sh`) | `infra/10-deploy-hub.sh` | `verify.sh hub` | 15–45 min |
| 3 | Vertex access + Claude image | `infra/20-model-access.sh`, then 🖐 enable the Claude models in **Model Garden** | `verify.sh vertex`, `verify.sh claude-image` | 5 min |
| 4 | 🖐 Custom IAP OAuth client (needed for any domain outside the org) | console steps, then `infra/55-iap-oauth-client.sh` | `verify.sh iap-oauth` | 5 min |
| 5 | Domain web access (IAP + hub sign-in policy) | `infra/50-web-access.sh` | browser login | 1 min |
| 6 | Hub project + GitHub secret | `30-hub-admin-access.sh enable`, `hub_cli hub projects create <repo-url>`, 🖐 PAT → `31-set-project-secret.sh`, `30… disable` | `verify.sh hubproject` | 3 min |
| 7 | Agent team templates | `infra/40-build-team-templates.sh`, then push to the hub (below) | `verify.sh team cost skills` | 3 min |
| 8 | Lead + standing roster | `hub_cli start acme-lead --type acme-lead --role full …` (kickoff below). The lead forms the roster itself | `verify.sh lead topology` | 5 min |
| 9 | Loop schedule | `infra/60-factory.sh tick-install` | `verify.sh loop` | 1 min |
| 10 | Budget guard | `infra/70-budget-guard.sh`, then the wiring test | `verify.sh budget` | 5 min |
| 11 | Observatory platform (optional) | `infra/80-observatory-platform.sh`, then 🖐 billing export and a GitHub read token | `verify.sh observatory` | 5 min |
| 12 | App platforms (optional, product-specific) | `infra/90-irrops-demo-platform.sh`, `infra/95-agent-engine-platform.sh` | `verify.sh irrops-demo agent-engine` | 5 min |
| ✅ | Everything | `infra/verify.sh all` (+ `e2e` once the app is deployed) | all PASS | — |

## Details and pitfalls per step

### 1. Project bootstrap
- **Default VPC:** hardened orgs often enforce `compute.skipDefaultNetworkCreation`.
  The script creates an auto-mode `default` VPC, because `deploy.sh` requires
  network and subnet `default`.
- **APIs:** the list includes `orgpolicy` and `cloudresourcemanager`; without
  them gcloud blocks on prompts or IAP grants fall back. Every script exports
  `CLOUDSDK_CORE_DISABLE_PROMPTS=1`.

### 2. Hub deploy
- **Pinned deployer:** `deploy.sh` comes from a clean shallow clone at
  `SCION_VERSION` (`.cache/`), never from a local Scion checkout with
  uncommitted edits.
- **Zone:** `deploy.sh` always picks the region's *first* zone. The script
  patches that line idempotently so it honours `DEPLOY_ZONE`, which got us
  past an `n2-standard-16` stockout.
- **Run headless:** `--config infra/deploy-config.json </dev/null`. Missing
  fields then fail instead of prompting.

### 3. Models
- **Seeded defaults:** `deploy.sh` grants the VM SA `aiplatform.user` and seeds
  hub env `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION=global`.
- **Claude image:** `deploy.sh` builds only the Antigravity image. Build
  `scion-claude` on the VM from `/opt/scion-source`.
- **Enable the exact models:** enable exactly the Claude models the templates
  use. The harness default background model was `claude-haiku-4-5`; we enabled
  only 5.5, so the Claude templates set
  `ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-5-5`.
- **Verify each model:** `verify.sh vertex` probes each model with the VM's
  metadata token.

### 4–5. Access
- **External domains:** Google-managed IAP OAuth clients admit **only users
  inside the project's org**. For external domains, configure a **custom OAuth
  client**:
  1. Google Auth Platform: Branding, then Audience **External** + **In
     production**.
  2. A Web client with redirect URI
     `https://iap.googleapis.com/v1/oauth/clientIds/<ID>:handleRedirect`.
  3. `infra/55-iap-oauth-client.sh`, which prompts for the ID and secret and
     keeps both in Secret Manager.
- **Re-apply after redeploys:** `deploy.sh` rewrites `settings.yaml`, so re-run
  `50-web-access.sh` and `55-iap-oauth-client.sh --reapply` after every
  redeploy.

### 6. Hub project and GitHub token
- **Use the CLI, not the UI:** in proxy mode the hub has no scripted login, and
  UATs can't create projects or secrets. `30-hub-admin-access.sh enable` adds a
  federation issuer that trusts gcloud ID tokens for `ORG_DOMAIN`. Then
  `hub_cli …` runs on the VM. **Always `disable` afterwards.**
- **Token permissions:** a fine-grained PAT on the repo with Contents, Pull
  requests, Issues and **Workflows** read/write. Issues are needed for the
  backlog; Workflows is needed to push `.github/workflows/*`.
- **Pass the secret over stdin:** `scion hub secret set` only accepts the value
  as an argument, or as `@file` (which base64-encodes it into a file secret).
  `31-set-project-secret.sh` instead PUTs it to the API from stdin.

### 7. Templates
`infra/40-build-team-templates.sh` composes
`factory/team/{team-rules.md,roles/*.md}` with upstream `agent-team@AGENT_TEAM_SHA`
into `factory/templates/acme-*`. Push them to the hub at **global** scope:

```bash
infra/30-hub-admin-access.sh enable
source infra/lib.sh
gcloud compute scp --recurse factory/templates/acme-* "${VM_NAME}:~/.scion/templates/" \
  --zone="$(resolve_zone)" --project="${PROJECT_ID}" --tunnel-through-iap
for d in factory/templates/acme-*; do hub_cli --global templates push "$(basename "$d")"; done
infra/30-hub-admin-access.sh disable
```

Never put generated templates under `.scion/templates/` in the product repo.
Agents' clones would treat them as project templates and shadow the hub's.

### 8. Kickoff
Start only the lead. It spawns everyone else, so the lineage graph shows the
tree:

```bash
hub_cli --project "${HUB_PROJECT_SLUG}" start acme-lead --type acme-lead --role full --no-wait \
  "You are acme-lead. Read your instructions and the product brief, form the standing roster, \
   create the pinned issue titled exactly: 🏭 Factory status, plan the MVP as issues, then start the loop."
```

### 10. Budget guard
- **What it does:** at 100% of **actual** spend it suspends every agent
  (`scion suspend --all`). It **never stops the VM**.
- **Test after every change:** publish a below-budget message and expect the
  guard log `cost=… -> no action` with HTTP 204. Our first guard silently
  rejected every alert because of the push audience.
- **Rotate tokens:** `ROTATE_TOKEN=1 infra/70-budget-guard.sh` (90 days).

### 11. Observatory
- **Agents build it:** the app itself is built by the factory from a brief
  issue plus [`docs/observatory/platform-contract.md`](../observatory/platform-contract.md).
- **Manual steps:**
  1. Turn on the Billing **standard usage cost export** to dataset
     `billing_export`.
  2. Store the GitHub token (read-only is enough):
     `read -rs T && printf %s "$T" | gcloud secrets versions add acme-observatory-github-token --data-file=-`

## Day-2 operations
See [05-cost-and-safety.md](05-cost-and-safety.md) for `pause`/`resume` and the
budget. For this factory's specifics (URLs, resource inventory) see
[`docs/setup/`](../setup/).
