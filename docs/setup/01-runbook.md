# Runbook — how the factory was built

Every command that was actually run to build the factory, in order, with its
purpose. Commands use the dedicated gcloud configuration `acme-factory`
(`CLOUDSDK_ACTIVE_CONFIG_NAME=acme-factory`, set by `infra/lib.sh`), so the
operator's default gcloud config is not changed.

## 0. Prerequisites (operator workstation)

- `gcloud` authenticated as `admin@duboc.altostrat.com`
- `gh` authenticated as `duboc`
- `jq`, `git`, a checkout of `GoogleCloudPlatform/scion` (`SCION_SRC`)

```bash
gcloud config configurations create acme-factory --no-activate
gcloud config set account admin@duboc.altostrat.com --configuration=acme-factory
# Argolis machines use certificate-based access; mirror it in the new config:
gcloud config set context_aware/use_client_certificate true --configuration=acme-factory
gcloud config set context_aware/certificate_config_file_path /etc/certificate_config.json --configuration=acme-factory
```

## 1. Bootstrap the GCP project (`infra/00-bootstrap-project.sh`)

```bash
infra/00-bootstrap-project.sh
```

What it ran (idempotent; safe to re-run):

```bash
gcloud projects create acme-airlines-factory --folder=543080121395 --name=acme-airlines-factory
gcloud config set project acme-airlines-factory            # in the acme-factory config only
gcloud billing projects link acme-airlines-factory --billing-account=01820C-6329B4-8A7409
gcloud services enable compute.googleapis.com run.googleapis.com iap.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com \
  iamcredentials.googleapis.com logging.googleapis.com monitoring.googleapis.com \
  orgpolicy.googleapis.com cloudresourcemanager.googleapis.com
gcloud org-policies describe <constraint> --project=acme-airlines-factory --effective   # informational
gcloud compute networks create default --project=acme-airlines-factory --subnet-mode=auto
```

Result: project `acme-airlines-factory` (number `493084803309`), billing enabled,
APIs enabled, auto-mode `default` network created (the org enforces
`compute.skipDefaultNetworkCreation`, so the project had no network).

Verify: `infra/verify.sh project` → `PASS project`.

## 2. Deploy the Hub (`infra/10-deploy-hub.sh`)

```bash
infra/10-deploy-hub.sh            # DEPLOY_ZONE defaults to us-central1-a
```

What it does:

1. Shallow-clones `GoogleCloudPlatform/scion` at tag `nightly-20261009` into
   `.cache/` (git-ignored) so the deploy script matches the binary it installs.
2. Applies a one-line patch so the zone honours `DEPLOY_ZONE` (see
   [troubleshooting](03-troubleshooting.md#n2-standard-16-stockout-in-us-central1-c)).
3. Runs Scion's supported deployer headlessly:

   ```bash
   ./scripts/single-node-vm/deploy.sh --version nightly-20261009 \
     --config infra/deploy-config.json </dev/null
   ```

deploy.sh then (all idempotent): enables APIs, creates the hub VM service
account (`logging.logWriter`, `monitoring.metricWriter`, `cloudtrace.agent`,
`artifactregistry.writer`, `aiplatform.user`, `iam.serviceAccountAdmin`) and the
proxy service account, grants the deployer `iap.tunnelResourceAccessor`,
creates Cloud Router + NAT, firewall rules (IAP SSH, proxy→VM tcp:8080), the
Shielded VM (no public IP), installs the scion binary + systemd unit, seeds
hub env vars `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION=global`, builds
`core-base`, `scion-base`, `scion-antigravity` images on the VM, builds and
deploys the Cloud Run IAP proxy, grants IAP access to the deployer and switches
the hub to `auth.mode: proxy`.

Result:

- VM `scion-hub-acme` in `us-central1-a` (n2-standard-16, 500 GB)
- Access URL: <https://scion-hub-acme-iap-proxy-vdhcml5ceq-uc.a.run.app>
- Run time ≈ 16 min (images built in ~5 min on n2-standard-16)

Verify: `infra/verify.sh hub` → `PASS hub`.

## 3. Vertex AI (global) access + Claude image (`infra/20-model-access.sh`)

```bash
infra/20-model-access.sh
```

What it does (idempotent):

```bash
gcloud projects add-iam-policy-binding acme-airlines-factory \
  --member=serviceAccount:scion-hub-acme@acme-airlines-factory.iam.gserviceaccount.com \
  --role=roles/aiplatform.user --condition=None      # already granted by deploy.sh; re-asserted
# on the VM, in /opt/scion-source (same tag as the hub):
sudo docker build -t scion-claude:latest --build-arg BASE_IMAGE=scion-base:latest \
  -f harnesses/claude/Dockerfile harnesses/claude/
sudo docker tag scion-claude:latest localhost/scion/scion-claude:latest
```

Hub-scoped env vars seen by every agent (seeded by deploy.sh):

| Key | Value |
|-----|-------|
| `GOOGLE_CLOUD_PROJECT` | `acme-airlines-factory` |
| `GOOGLE_CLOUD_LOCATION` | `global` |

The Claude harness maps these to `CLAUDE_CODE_USE_VERTEX=1`,
`ANTHROPIC_VERTEX_PROJECT_ID` and `CLOUD_ML_REGION` (from
`GOOGLE_CLOUD_LOCATION` → `global`). Antigravity uses ADC from the VM metadata
server with the same variables. No API keys are used.

### Manual step — enable Claude in Vertex AI Model Garden

Console → **Vertex AI → Model Garden** → search "Claude" → open each model →
**Enable** (accept Anthropic terms), for project `acme-airlines-factory`:

- Claude Opus 5.5 (`claude-opus-5-5`) — default model of the scion Claude harness
- Claude Sonnet 5.5 (`claude-sonnet-5-5`) — backend developer model
- Claude Haiku 5.5 (`claude-haiku-5-5`) — background/small model. The harness default
  `claude-haiku-4-5` is **not** enabled, so Claude templates set
  `ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-5-5`.

Verify: `infra/verify.sh vertex` (calls Gemini `generateContent` and Claude
`rawPredict` on `locations/global` from the VM with its service account) and
`infra/verify.sh claude-image`.

## 4. Register acme-airlines on the Hub (manual, web UI)

The Hub runs with IAP proxy auth; the simplest path is the web UI at the access URL.

1. Open <https://scion-hub-acme-iap-proxy-vdhcml5ceq-uc.a.run.app> and sign in
   as `admin@duboc.altostrat.com` (auto-promoted to super-admin).
2. **Create the GitHub token** — GitHub → Settings → Developer settings →
   Personal access tokens → **Fine-grained tokens** → Generate new token:
   - Resource owner: `duboc`; Repository access: **Only select repositories** → `duboc/acme-airlines`
   - Permissions: **Contents: Read and write**, **Pull requests: Read and write**
     (Metadata: Read-only is added automatically)
   - Expiration: 90 days
3. **Create the project** — Projects → New project → from Git URL
   `https://github.com/duboc/acme-airlines.git` (branch `main`; the hub derives slug `duboc-acme-airlines`).
4. **Add the secret** — open the project → Settings → Secrets → Add Secret:
   key `GITHUB_TOKEN`, value = the token from step 2. Never paste it anywhere else.

Verify: `infra/verify.sh hubproject` (read-only query of the hub DB on the VM;
checks the project exists and that a project secret *named* `GITHUB_TOKEN` exists).

> **Actual path taken (addendum A):** instead of the web UI, the project and
> secret were created from the CLI as the admin user, using temporary hub
> federation (§5). The web-UI steps above remain a valid alternative.

## 5. Hub admin access, project, secret, smoke test, team (addendum A)

### 5.1 Temporary admin access (`infra/30-hub-admin-access.sh`)

In IAP proxy-auth mode the hub has no non-interactive CLI login. User access
tokens can't create projects or secrets, and dev mode would create resources
owned by a pseudo-user. So, for the duration of the admin work, this script
adds a federation trusted issuer that accepts Google ID tokens minted by the
gcloud CLI for `duboc.altostrat.com` users. The block is marked, and the script
backs up `settings.yaml` before changing it:

```yaml
server:
  federation:
    enabled: true
    trusted_issuers:
      - issuer_url: "https://accounts.google.com"
        jwks_url: "https://www.googleapis.com/oauth2/v3/certs"
        issuer_type: "user"
        expected_audience: "32555940559.apps.googleusercontent.com"   # gcloud CLI client
        allowed_domains: ["duboc.altostrat.com"]
```

```bash
infra/30-hub-admin-access.sh enable     # adds the block, restarts scion-hub, waits for /healthz
bash -c 'source infra/lib.sh; hub_cli hub status'   # → User: admin@duboc.altostrat.com, Role: admin
infra/30-hub-admin-access.sh disable    # removes the block when done
```

`hub_cli` (in `infra/lib.sh`) sends `gcloud auth print-identity-token` over SSH
**stdin** and runs `scion …` on the VM against `http://localhost:8080`.
IAP keeps guarding the public URL the whole time.

### 5.2 Project and GitHub secret

```bash
bash -c 'source infra/lib.sh; hub_cli hub projects create https://github.com/duboc/acme-airlines.git'
# → ID 61b6ae3d-9335-4360-a259-5b306dc229f6, slug duboc-acme-airlines
printf '%s' "$GITHUB_PAT" | infra/31-set-project-secret.sh 61b6ae3d-9335-4360-a259-5b306dc229f6 GITHUB_TOKEN
# → HTTP 200 (PUT /api/v1/secrets/GITHUB_TOKEN, scope=project, encoding=raw, injectionMode=always)
```

`scion hub secret set` only takes the value as an argument, or as `@file`,
which it base64-encodes into a file secret. So the helper sends the value
over SSH stdin and PUTs it to the Hub API from memory. It never appears in
argv, on disk or in logs.

GitHub token permissions (fine-grained, `duboc/acme-airlines` only):
Contents RW, Pull requests RW, Issues RW, Workflows RW (Metadata R is implicit).

Verify: `infra/verify.sh hubproject` → `PASS hubproject`.

### 5.3 Smoke test

```bash
bash -c 'source infra/lib.sh; hub_cli start smoke-gemini --project duboc-acme-airlines --harness-config antigravity --no-wait "<task>"'
HUB_CLI_STDIN=$'env:\n  ANTHROPIC_DEFAULT_HAIKU_MODEL: claude-haiku-5-5\n' \
  bash -c 'source infra/lib.sh; hub_cli start smoke-claude --project duboc-acme-airlines --harness-config claude --config - --no-wait "<task>"'
```

Both agents cloned the repo with `GITHUB_TOKEN` and opened PRs #1 and #2. The
Claude agent reported `CLOUD_ML_REGION=global`, `CLAUDE_CODE_USE_VERTEX=1` and
`ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-5-5`. Results are recorded in
[`docs/factory/smoke-tests.md`](../factory/smoke-tests.md); the PRs were closed
and the agents deleted.

Verify: `infra/verify.sh smoke` → `PASS smoke`.

### 5.4 Team templates

```bash
infra/40-build-team-templates.sh     # factory/team/* + upstream agent-team@3650f1b → factory/templates/acme-*
# upload to the hub at global scope (admin access enabled):
gcloud compute scp --recurse factory/templates/acme-* scion-hub-acme:~/factory/templates/ \
  --zone=us-central1-a --project=acme-airlines-factory --tunnel-through-iap
for t in acme-lead acme-backend acme-frontend acme-qa acme-reviewer; do
  bash -c "source infra/lib.sh; hub_cli --global templates push $t"
done
```

Verify: `infra/verify.sh team` → `PASS team`.

### 5.5 Launch the lead

```bash
bash -c 'source infra/lib.sh; hub_cli start acme-lead --project duboc-acme-airlines \
  --type acme-lead --role full --no-wait "<kickoff: begin Phase 0 planning>"'
```

`--role full` lets the lead spawn and manage workers; it starts workers with
`--role baseline`. Verify: `infra/verify.sh lead` → `PASS lead`.
How to work with the team: [`docs/factory/team.md`](../factory/team.md).

## 6. Full role catalog and domain web access

### 6.1 Web access for @google.com and @duboc.altostrat.com (`infra/50-web-access.sh`)

```bash
infra/50-web-access.sh     # WEB_DOMAINS="google.com duboc.altostrat.com" DEFAULT_USER_ROLE=member (defaults)
```

1. **IAP** (who can reach the hub):
   ```bash
   gcloud iap web add-iam-policy-binding --resource-type=cloud-run \
     --service=scion-hub-acme-iap-proxy --region=us-central1 \
     --member=domain:<d> --role=roles/iap.httpsResourceAccessor
   ```
2. **Hub sign-in policy** (who the hub accepts): a marked block inserted under
   `server.auth` in `settings.yaml`, followed by a hub restart.
   ```yaml
   user_access_mode: domain_restricted
   authorized_domains: ["google.com", "duboc.altostrat.com"]
   default_user_role: member
   ```

Re-run after every `infra/10-deploy-hub.sh`, because deploy.sh rewrites
`settings.yaml`. Risks and options are in [ADR 0011](../decisions/0011-full-role-catalog-and-domain-web-access.md).

### 6.3 Custom IAP OAuth client (required for @google.com)

The Google-managed OAuth client admits only `duboc.altostrat.com` users (see
troubleshooting). One-time console steps (they can't be scripted), in project
`acme-airlines-factory`:

1. **Google Auth Platform → Branding:** app name `ACME Factory`, support email
   and developer contact `admin@duboc.altostrat.com`.
2. **Audience:** user type **External**, then **Publish app** (status **In
   production**). With only the basic `openid`/`email`/`profile` scopes, no
   verification is required. In *Testing* status, only listed test users get in.
3. **Clients → Create client:** type **Web application**, name `acme-iap`.
   Create it, then add this authorized redirect URI:
   `https://iap.googleapis.com/v1/oauth/clientIds/<CLIENT_ID>:handleRedirect`.
   Copy the client ID and secret.
4. Apply the client to both IAP services. The secret goes over stdin and is
   kept in Secret Manager:
   ```bash
   read -rs SECRET && printf '%s' "$SECRET" | \
     IAP_CLIENT_ID=<CLIENT_ID> infra/55-iap-oauth-client.sh; unset SECRET
   ```

Check: `infra/verify.sh iap-oauth`. Re-apply after a hub re-deploy with
`infra/55-iap-oauth-client.sh --reapply`.

### 6.2 Thirteen role templates

```bash
infra/40-build-team-templates.sh       # 13 roles → factory/templates/acme-*
infra/30-hub-admin-access.sh enable
gcloud compute scp --recurse factory/templates/acme-* scion-hub-acme:~/factory/templates/ \
  --zone=us-central1-a --project=acme-airlines-factory --tunnel-through-iap
for d in factory/templates/acme-*; do bash -c "source infra/lib.sh; hub_cli --global templates push $(basename "$d")"; done
infra/30-hub-admin-access.sh disable
```

Verify: `infra/verify.sh team` → `PASS team` (all 13 registered).

## 7. Autonomous loop, cost controls, budget guard (addendum B)

Spec: [addendum B](../superpowers/specs/2026-10-09-addendum-b-autonomous-loop.md).

### 7.1 Templates: models, caps, impeccable
`infra/40-build-team-templates.sh`:
- Opus: lead, eng-manager, architect
- Sonnet: backend, frontend, qa
- Gemini Flash (antigravity `medium`): everything else
- `max_duration`/`max_turns` caps on every non-lead role
- `acme-frontend` gets
  `gh://pbakaus/impeccable/.claude/skills/impeccable@d631a8827f99414d2b6daba4ef08b7f8701751d7`

Checks:
- `infra/verify.sh cost`: committed templates match the mapping and have caps.
- `infra/verify.sh skills`: impeccable is in the hub-stored template.

### 7.2 Budget guard
```bash
infra/70-budget-guard.sh      # BUDGET_USD=1000 by default
```
What it creates:
- service accounts `acme-budget-guard` (no Compute roles) and `acme-builder`
  (Cloud Build)
- a project-scoped hub user access token (`agent:list`, `agent:lifecycle`), piped
  straight into Secret Manager `acme-guard-hub-token`
- Cloud Run `acme-budget-guard` (Python plus the pinned, checksum-verified
  scion CLI; Direct VPC egress to the hub; tests in `infra/budget-guard/`). At
  100% actual spend it suspends all agents. **It never stops the VM.**
- Pub/Sub topic `acme-budget-alerts` with an authenticated push subscription
- the budget, with 50/90/100% thresholds

Two problems hit here are in troubleshooting.

Check: `infra/verify.sh budget`. Plus the end-to-end wiring test in
[operations](02-operations.md#budget-guardrail-infra70-budget-guardsh).

### 7.3 Loop and rollout
In one admin-access window:
1. Push the 13 templates.
2. Create the schedule (or run `infra/60-factory.sh tick-install`):
   ```bash
   hub_cli --project duboc-acme-airlines schedule create-recurring --name acme-factory-tick \
     --cron "*/30 * * * *" --type message --agent acme-lead --message "TICK: …"
   ```
3. Message `acme-lead` with the new operating model. Its first pass: merge PR
   #25, align the backlog to the full brief, recreate the standing roster
   under the new models, create the pinned **🏭 Factory status** issue, and
   start the loop.

Check: `infra/verify.sh loop` (schedule active, targets acme-lead, at most one
tick per 30 minutes; `cron_interval_ok` is unit-tested).

## 8. Observatory platform (addendum C)

Spec: [addendum C](../superpowers/specs/2026-10-09-addendum-c-observatory.md).
The agents build against [the platform contract](../observatory/platform-contract.md).

```bash
infra/80-observatory-platform.sh
```
Idempotent. What it creates:
- BigQuery `billing_export` and `factory_ops` (US)
- service accounts `acme-observatory` (read-only), `acme-observatory-collector`
  and `acme-observatory-deployer`
- secrets `acme-observatory-hub-token` (a hub UAT with `agent:list`/`agent:read`,
  minted in an admin-access window and piped straight into Secret Manager) and
  `acme-observatory-github-token` (empty until the product owner adds it)
- Artifact Registry `observatory`
- WIF pool `github` / provider `github-actions`, restricted to
  `duboc/acme-airlines` on `refs/heads/main`
- the placeholder Cloud Run service `acme-observatory` with **IAP** for
  `google.com` and `duboc.altostrat.com`
- the placeholder job `acme-observatory-collector` (VPC egress to the hub)
- Cloud Scheduler `acme-observatory-collect` (`*/5`)
- the GitHub Actions variables `OBSERVATORY_*`

Dashboard URL: <https://acme-observatory-vdhcml5ceq-uc.a.run.app> (IAP).
Then the brief issue
[#72](https://github.com/duboc/acme-airlines/issues/72) was filed for the lead.

Check: `infra/verify.sh observatory`.

### Manual steps (product owner)

1. **Billing export:** console → Billing → *Billing export* → **Standard usage
   cost** → Edit settings → project `acme-airlines-factory`, dataset
   `billing_export` → Save. Data starts flowing after a few hours.
2. **Read-only GitHub token:** a fine-grained PAT on `duboc/acme-airlines`
   with Contents, Issues, Pull requests, Actions and Metadata all **read-only**.
   Store it without echoing it:
   ```bash
   read -rs GH_RO && printf '%s' "$GH_RO" | gcloud secrets versions add acme-observatory-github-token \
     --project=acme-airlines-factory --data-file=- && unset GH_RO
   ```

## 9. IRROPS demo platform (mocked scenarios behind IAP)

The product owner wants to be able to try the IRROPS MVP with mocked scenarios. It runs as **one Cloud
Run service behind IAP** (`acme-irrops-demo`, `LLM_MODE=fake`, in-memory state). The Observatory deployer
only has `run.developer`, so the operator creates the platform once:

```bash
infra/90-irrops-demo-platform.sh
```

Idempotent. It creates, and grants nothing else:
- Artifact Registry `irrops` (`us-central1`)
- service account `acme-irrops-demo` with **no project roles** (fake mode needs no GCP access)
- Cloud Run `acme-irrops-demo`: placeholder image, `--no-allow-unauthenticated --iap`,
  `--max-instances=1`; IAP service agent as `run.invoker`; `iap.httpsResourceAccessor` for the domains
  `google.com` and `duboc.altostrat.com`
- for the existing `acme-observatory-deployer`: `artifactregistry.writer` on `irrops` and
  `iam.serviceAccountUser` on `acme-irrops-demo`
- GitHub Actions variables `IRROPS_DEMO_SERVICE` and `IRROPS_AR_REPO` (the existing
  `OBSERVATORY_WIF_PROVIDER` / `OBSERVATORY_DEPLOYER_SA` are reused)
- if the custom IAP OAuth client from `55-iap-oauth-client.sh` exists, it is applied to the new
  service (external domains such as `google.com`); otherwise run `infra/55-iap-oauth-client.sh`
  afterwards (it now includes `acme-irrops-demo` and skips services that do not exist yet)

It prints the IAP service URL at the end. Check: `infra/verify.sh irrops-demo` (IAP on, one instance,
not public, runtime SA without project roles). Offline checks of the script itself:
`infra/tests/test_irrops_demo_script.sh`. Image build and deploy of the app come with the demo deploy
workflow; the local alternative is in [deploy.md](../product/deploy.md).
