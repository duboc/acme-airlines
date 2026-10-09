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
