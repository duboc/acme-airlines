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

_Filled in during bootstrap (Task 2)._

## Resource inventory

_Filled in after hub deployment (Task 3)._

## Cost

_Filled in in Task 7._
