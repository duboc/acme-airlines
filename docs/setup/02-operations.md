# Operations

All commands assume the repo root and `CLOUDSDK_ACTIVE_CONFIG_NAME=acme-factory`
(set automatically by `infra/lib.sh`). Zone: `us-central1-a`.

## Access

| What | How |
|------|-----|
| Web UI | <https://scion-hub-acme-iap-proxy-vdhcml5ceq-uc.a.run.app> (Google login via IAP) |
| SSH | `gcloud compute ssh scion-hub-acme --zone=us-central1-a --project=acme-airlines-factory --tunnel-through-iap` |
| Hub logs | add `--command='sudo journalctl -u scion-hub.service -f'` to the SSH command |
| Operator CLI | `infra/30-hub-admin-access.sh enable`, then `bash -c 'source infra/lib.sh; hub_cli <scion args>'`, then `… disable` |
| Health | `infra/verify.sh all` |

### Grant another person browser access

```bash
gcloud iap web add-iam-policy-binding --resource-type=cloud-run \
  --service=scion-hub-acme-iap-proxy --region=us-central1 --project=acme-airlines-factory \
  --member=user:colleague@duboc.altostrat.com --role=roles/iap.httpsResourceAccessor
```

## Stop / start (save money)

Agents and the hub stop with the VM; state (SQLite, workspaces) is on the disk.

```bash
gcloud compute instances stop  scion-hub-acme --zone=us-central1-a --project=acme-airlines-factory
gcloud compute instances start scion-hub-acme --zone=us-central1-a --project=acme-airlines-factory
```

If `start` fails with a stockout, wait and retry (see troubleshooting).
While the VM is stopped you pay only for the disk (~$85/month for 500 GB
pd-balanced) and the idle Cloud Run proxy (≈ $0).

## Cost (us-central1, list prices, approximate)

| Item | Running 24/7 | Notes |
|------|--------------|-------|
| n2-standard-16 VM | ~$570/month | Sustained-use discount applies automatically |
| 500 GB pd-balanced | ~$50/month | |
| Cloud NAT + egress | ~$35/month + egress | |
| Cloud Run IAP proxy | ~$0–5/month | Scales to zero |
| Vertex AI (Gemini + Claude) | usage-based | The main variable cost with an active team; watch Billing → Reports |

Set a budget alert on the billing account for project `acme-airlines-factory`.

## Upgrades

`update_policy: notify` — the admin UI shows a banner when a new release is
available; apply it from the UI. To redeploy a specific version from the
operator machine: `SCION_VERSION=<tag> infra/10-deploy-hub.sh` (idempotent;
re-applies the zone patch). After an upgrade, rebuild the Claude image if the
harness changed: delete it on the VM (`sudo docker rmi localhost/scion/scion-claude:latest`)
and run `infra/20-model-access.sh`.

## Rotating the GitHub token

1. Create a new fine-grained token (same repo + permissions as in runbook §5.2).
2. `infra/30-hub-admin-access.sh enable`
3. `printf '%s' "$NEW_PAT" | infra/31-set-project-secret.sh 61b6ae3d-9335-4360-a259-5b306dc229f6 GITHUB_TOKEN`
   (or Web UI → project → Settings → Secrets).
4. `infra/30-hub-admin-access.sh disable`; revoke the old token on GitHub.
   Running agents get the new value on their next start.

## The agent team

See [docs/factory/team.md](../factory/team.md).
