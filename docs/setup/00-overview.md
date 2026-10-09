# Factory Overview

The acme-airlines software factory is a single-node
[Scion](https://github.com/GoogleCloudPlatform/scion) deployment on Google Cloud.
Design: [spec](../superpowers/specs/2026-10-09-scion-factory-design.md).

## Architecture

```mermaid
flowchart LR
  user["Developer browser / scion CLI"] -->|Google login| iap["IAP"]
  iap --> proxy["Cloud Run: scion-hub-acme-iap-proxy"]
  proxy -->|"Direct VPC egress tcp:8080"| vm
  subgraph proj["GCP project acme-airlines-factory (us-central1)"]
    proxy
    subgraph vm["GCE VM scion-hub-acme (no public IP)"]
      hub["scion server: Hub + Runtime Broker (systemd)"]
      db[("SQLite hub.db")]
      a1["Agent container: Antigravity"]
      a2["Agent container: Claude"]
      hub --- db
      a1 -->|localhost:8080| hub
      a2 -->|localhost:8080| hub
    end
    nat["Cloud NAT"]
    vertex["Vertex AI (global endpoint): Gemini + Claude"]
  end
  a1 & a2 -->|egress| nat --> gh["GitHub duboc/acme-airlines"]
  a1 & a2 -->|"ADC via VM SA"| vertex
```

## Project

| Field | Value |
|-------|-------|
| Project ID | `acme-airlines-factory` |
| Project number | `493084803309` |
| Parent | folder `dubofolder` (`543080121395`) in org `duboc.altostrat.com` |
| Billing | `01820C-6329B4-8A7409` (Argolis Billing) |
| Region | `us-central1` |
| gcloud config | `acme-factory` (`CLOUDSDK_ACTIVE_CONFIG_NAME=acme-factory`) |

### Effective org policies (checked 2026-10-09)

| Constraint | Effective | Impact |
|------------|-----------|--------|
| `compute.skipDefaultNetworkCreation` | enforced | No `default` VPC; bootstrap creates an auto-mode one. |
| `compute.requireShieldedVm` | not enforced | deploy.sh uses Shielded VM anyway. |
| `compute.vmExternalIpAccess` | allow all | VM still has no public IP by design. |
| `iam.allowedPolicyMemberDomains` | allow all | — |
| `iam.disableServiceAccountKeyCreation` | not enforced | We don't create SA keys regardless. |
| `compute.requireOsLogin` | not enforced | — |
| `run.allowedIngress` | allow all | — |
| `gcp.resourceLocations` | allow all | — |

## Resource inventory

_Filled in after hub deployment (Task 3)._

## Cost

_Filled in in Task 7._
