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
- Claude Haiku 4.5 (`claude-haiku-4-5`) — background/small model

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
   `https://github.com/duboc/acme-airlines.git` (slug `acme-airlines`, branch `main`).
4. **Add the secret** — open the project → Settings → Secrets → Add Secret:
   key `GITHUB_TOKEN`, value = the token from step 2. Never paste it anywhere else.

Verify: `infra/verify.sh hubproject` (read-only query of the hub DB on the VM;
checks the project exists and that a project secret *named* `GITHUB_TOKEN` exists).
