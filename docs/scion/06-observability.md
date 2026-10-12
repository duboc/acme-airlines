# 6. Observability and verification

## `infra/verify.sh`: the factory's test suite

`infra/verify.sh <check>` prints `PASS`/`FAIL` with a reason. `all` runs the
platform checks; `e2e` runs the live product path. Parsing logic is
unit-tested in `infra/tests/test_verify.sh`. Every check was seen failing
(RED) before its fix.

| Check | Asserts |
|---|---|
| `project` | Project exists, billing on, APIs enabled, `default` subnet |
| `hub` | VM running, `scion-hub` active, `/healthz`, Antigravity image, IAP enforcing |
| `vertex` | From the VM with its SA: Gemini and each enabled Claude model return 200 on `global` |
| `claude-image` | `scion-claude` image present on the VM |
| `hubproject` | Hub project for `GITHUB_REPO` exists with secret `GITHUB_TOKEN` (name only) |
| `smoke` | Smoke PRs from both harnesses exist |
| `team` | All 13 `acme-*` templates registered on the hub |
| `cost` | Committed templates match the model mapping and carry caps |
| `skills` | Hub-stored `acme-frontend` has a mounted impeccable `SKILL.md`, and no nested `gh://` URI |
| `lead` | `acme-lead` container running |
| `topology` | Every live `acme-*` agent descends from `acme-lead`; at least 11 present |
| `loop` | `acme-factory-tick` active, targets the lead, interval ≥ 30 min |
| `budget` | Budget with a 100% threshold wired to the topic, the guard has the hub token, and the guard SA has no role on the VM |
| `observatory` | SAs, datasets, WIF, AR, secrets, service, job, scheduler, repo variables, `cloudtrace.user` |
| `iap-oauth` | Custom OAuth client on the IAP services |
| `agent-engine` | Runtime SA roles, staging bucket, deployer roles (incl. `legacyBucketReader`), demo query role, service-agent token creator |
| `irrops-demo` | Demo service behind IAP; its SA holds only `aiplatform.user` |
| `engines` *(e2e)* | Exactly one `acme-customer-agent` and one `acme-sales-agent`, running as `acme-agent-engine`, with no reserved env |
| `traces` *(e2e)* | Cloud Trace has ADK agent spans (`invoke_agent`/`call_llm`/`execute_tool`) in the last 24h |
| `demo-engine` *(e2e)* | The demo runs with `ENGINE_MODE=agent-engine` |

## Where to look

| Question | Place |
|---|---|
| What is the team doing right now? | Pinned issue **🏭 Factory status** (rewritten every loop pass) |
| What does the team look like? | Hub UI → Project → Agents → **graph** view (lineage tree under the lead) |
| What did an agent do? | Hub UI agent terminal / `hub_cli look <agent>`, plus its structured comments on the issue |
| What does it cost, and how much do we use models? | **Observatory**: spend (billing export), model usage (Monitoring), agent runs and GitHub flow (collector to BigQuery), budget and guard actions |
| Is the collector healthy? | The Observatory health indicator (latest snapshot age); `gcloud run jobs executions list --job=acme-observatory-collector` |
| Are the app's agents working? | Agent Engine console (sessions, traces), plus Cloud Trace (`invoke_agent`, `call_llm`, `execute_tool` spans) and Cloud Logging |
| Hub/broker logs | `gcloud compute ssh <vm> --tunnel-through-iap --command='sudo journalctl -u scion-hub -f'` |
| Did the budget guard act? | Cloud Logging: `resource.labels.service_name="acme-budget-guard"`, with lines `cost=… budget=… -> …` |

## Optional: export the Scion agents' own telemetry

Scion's `sciontool` forwards OTel (traces, metrics, redacted logs) per agent.
On this VM the agents already have ADC through the metadata server, and the VM
SA has `cloudtrace.agent`, `logging.logWriter` and `monitoring.metricWriter`.
Enabling it is a hub `settings.yaml` change:

```yaml
telemetry:
  enabled: true
  cloud: { enabled: true, gcp_project_id: "<project>", endpoint: "monitoring.googleapis.com:443", protocol: grpc }
  filter: { events: { exclude: ["agent.user.prompt"] } }
```

This was **not** enabled in this factory; the product owner chose Agent
Engine tracing for the app agents instead. See Scion's
`hosted/single-node/observability` docs.
