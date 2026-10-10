# Teardown

> [!CAUTION]
> Teardown deletes the hub VM, including its SQLite database, agent workspaces
> and any unpushed agent work. Make sure everything you care about is pushed to
> GitHub first.

## Option A — stop token spend, keep everything running

`infra/60-factory.sh pause` (agents suspended; hub, UI and VM keep running). The VM is never stopped as a cost measure.

## Option B — remove the factory, keep the project

```bash
infra/90-teardown.sh
```

Runs Scion's `deploy.sh --delete` (same config), which removes: Cloud Run IAP
proxy, VM, Cloud NAT + router (if it created them), both service accounts, and
both firewall rules. IAP access bindings are region-scoped and are **not**
removed — list them with:

```bash
gcloud iap web get-iam-policy --project=acme-airlines-factory --region=us-central1 --resource-type=cloud-run
```

Also left behind: the `default` network, the `cloud-run-source-deploy` Artifact
Registry repo, enabled APIs.

## Option C — delete the whole project

```bash
infra/90-teardown.sh --delete-project
```

Two typed confirmations. The project is recoverable for 30 days
(`gcloud projects undelete acme-airlines-factory`).

## Afterwards

- Revoke the GitHub fine-grained token used as `GITHUB_TOKEN`.
- Remove the local gcloud configuration: `gcloud config configurations delete acme-factory`.
