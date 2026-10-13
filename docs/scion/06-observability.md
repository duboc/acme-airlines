# 6. Observability and verification

## `infra/verify.sh`: the factory's test suite

`infra/verify.sh <check>` prints `PASS`/`FAIL` with a reason. `all` runs the
platform checks; `e2e` runs the live product path. Parsing logic is
unit-tested in `infra/tests/test_verify.sh`. Every check was seen failing
(RED) before its fix. For the full product and factory evaluation pyramid
(5-layer governance suite, Hypothesis policy oracle, TOCTOU race injection,
and live Vertex AI Agent Engine acceptance proof), see
[10-eval-methodology.md](10-eval-methodology.md).

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
| `webaccess` | Hub `default_user_role` is `viewer` (new web users cannot create agents) |
| `backups` | Daily snapshot policy (`7d` retention) attached to the hub VM boot disk |
| `watchdog` | `acme-factory-watchdog` service ready (not public), UAT secret enabled, `*/5` and `0 4 * * *` scheduler jobs active, PO alert policy enabled |
| `identity` | Dedicated `acme-agent@` SA with 4 least-privilege roles, VM SA has `tokenCreator` on `acme-agent@` and no `serviceAccountAdmin`, deployer `run.developer` resource-scoped, Hub default `block`, project default `assign`, all live agents in `assign` mode |
| `telemetry` | Scion Cloud Monitoring telemetry export enabled in Hub settings |
| `engines` *(e2e)* | Exactly one `acme-customer-agent` and one `acme-sales-agent`, running as `acme-agent-engine`, with no reserved env |
| `traces` *(e2e)* | Cloud Trace has ADK/`acme.*` agent spans (`invoke_agent`/`call_llm`/`execute_tool`/`agent_run`/`acme.*`) in the last 24h |
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

## Scion agent telemetry export (`infra/25-agent-identity.sh`)

Scion's `sciontool` forwards OTel metrics (`gen_ai.api.calls`, `scion.usage.tokens`)
and traces per agent, labelled with `scion_agent_id`, `scion_agent_slug`,
`scion_project_id`, `harness`, `model`, and `token_type`. Each agent runs in
`assign` mode as `acme-agent@<project>.iam.gserviceaccount.com` (which holds
`cloudtrace.agent`, `logging.logWriter` and `monitoring.metricWriter`), and
`infra/25-agent-identity.sh` configures Hub `settings.yaml`:

```yaml
telemetry:
  enabled: true
  cloud:
    enabled: true
    gcp_project_id: "<project>"
    endpoint: "monitoring.googleapis.com:443"
    protocol: "grpc"
  hub:
    enabled: true
    report_interval: "30s"
```

Verified by `infra/verify.sh telemetry`.
