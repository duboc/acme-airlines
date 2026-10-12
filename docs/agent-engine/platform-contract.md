# Agent Engine: platform contract (IRROPS agents)

This is what the team can rely on for running the IRROPS ADK agents on
**Vertex AI Agent Engine**. The operator provisioned it with
`infra/95-agent-engine-platform.sh`. Design:
[addendum D](../superpowers/specs/2026-10-12-addendum-d-agent-engine.md).

> [!IMPORTANT]
> Agents never change IAM, buckets, service accounts or WIF. If you need a
> platform change, file `status:blocked` + "needs operator".

## Resources

| Resource | Value |
|---|---|
| Project / Agent Engine location | `acme-airlines-factory` / `us-central1` (the resource location; regional by necessity) |
| Model endpoint | Vertex AI **`global`**, unchanged (`GOOGLE_CLOUD_LOCATION=global` inside the agent code for `google-genai`/ADK) |
| Runtime service account | `acme-agent-engine@acme-airlines-factory.iam.gserviceaccount.com`. Deploy engines with `service_account=` set to it. It has `aiplatform.user`, `cloudtrace.agent`, `logging.logWriter` and `monitoring.metricWriter`. |
| Staging bucket | `gs://acme-airlines-factory-agent-engine` |
| Deployer (GitHub Actions, WIF) | the existing `vars.OBSERVATORY_WIF_PROVIDER` + `vars.OBSERVATORY_DEPLOYER_SA`. It can create/update reasoning engines, write to the staging bucket, and act as the runtime SA. |
| Querying engines | `acme-irrops-demo` (the Cloud Run SA) has `aiplatform.user`. The demo API calls the engines with ADC. |
| GitHub variables | `AGENT_ENGINE_SA`, `AGENT_ENGINE_STAGING_BUCKET`, `AGENT_ENGINE_LOCATION` |

## Expected shape (owned by the team)

- **Two engines,** with stable display names, updated in place on each deploy
  (look up by display name, then update; never create duplicates):
  - `acme-customer-agent`: the Customer agent and its specialists (ADK app)
  - `acme-sales-agent`: the Sales agent. Its only outbound path is A2A to the
    Customer agent's three PII-free skills, and the guardrail stays enforced
    in code.
- **Single-instance Customer engine (demo limitation):** the Customer engine holds the
  mocked world and journeys in memory, so it is deployed with `min_instances=1` and
  `max_instances=1`. State is lost on restart and there is no horizontal scale. Do not
  assume durable state or more than one replica.
- **Mocks:** the mocked enterprise systems are packaged in process inside each
  engine. No external calls, and no MCP network servers are needed on Agent
  Engine.
- **Tracing:** deploy with `enable_tracing=True`, so traces go to Cloud Trace
  and logs to Cloud Logging. Add useful span attributes, such as the scenario
  and journey id. **Never put PII** (names, PNR contents) in span attributes or
  logs.
- **Environment on the engines:** `LLM_MODE=vertex`, `GOOGLE_CLOUD_PROJECT`,
  and `GOOGLE_CLOUD_LOCATION=global`. CI and unit tests keep `LLM_MODE=fake`;
  CI never deploys or calls engines from PRs.
- **Deploy workflow:** `.github/workflows/agent-engine-deploy.yml` runs on
  push to `main` (paths: the agent packages). It authenticates with WIF and
  deploys or updates both engines. Afterwards it writes the engine resource
  names to the demo service (an env var) or to a well-known location the demo
  reads.
- **The demo** (`acme-irrops-demo`) switches from in-process agents to
  `stream_query` against the engines, with `ENGINE_MODE=agent-engine|local`.
  `local` remains for dev and CI.
- **Observatory:** a new Agent Engine panel showing traces, latency and errors
  per engine, from Cloud Trace and Cloud Monitoring, using the existing
  read-only dashboard SA. If it needs `cloudtrace.user`, file "needs operator".
- **Proof:** an end-to-end check runs scenarios 1→5 on the deployed engines
  and asserts that traces exist for each turn (Cloud Trace query).
