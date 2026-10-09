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
  orgpolicy.googleapis.com
gcloud org-policies describe <constraint> --project=acme-airlines-factory --effective   # informational
gcloud compute networks create default --project=acme-airlines-factory --subnet-mode=auto
```

Result: project `acme-airlines-factory` (number `493084803309`), billing enabled,
APIs enabled, auto-mode `default` network created (the org enforces
`compute.skipDefaultNetworkCreation`, so the project had no network).

Verify: `infra/verify.sh project` → `PASS project`.
