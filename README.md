# acme-airlines

Home of the **acme-airlines** application and of the **Scion software factory** that
builds and evaluates it (modeled after [`duboc/scion-experiment`](https://github.com/duboc/scion-experiment)): two Scion projects running on a self-hosted [Scion](https://github.com/GoogleCloudPlatform/scion) Hub VM (`duboc-acme-airlines` for development on `main` and `acme-airlines-eval` for evaluation & the Observatory dashboard on `eval-dashboard`), with 10 standing agents (Claude Opus/Sonnet 5.5 + Antigravity Gemini 4 Argon) and 15-minute heartbeats.

## Layout

| Path | What |
|---|---|
| [`app/`](app/) | The acme-airlines multi-agent application (`app/backend`, `app/frontend`). |
| [`observatory/`](observatory/) | The evaluation & observability collector and dashboard (`acme-observatory`). |
| [`docs/scion/`](docs/scion/README.md) | Scion setup, 2-project architecture, 10 role templates, 15-minute heartbeat, and GitHub issue protocol. |
| [`docs/scion/templates/`](docs/scion/templates/) | Shared protocol (`_shared/agents.md`) + 10 role templates (`lead`, `backend`, `frontend`, `reviewer`, `deployer`, `eval-lead`, `factory-analyst`, `search-evaluator`, `ui-craftsman`, `eval-deployer`). |
| [`docs/product/`](docs/product/) | Product brief ([IRROPS use case](docs/product/irrops-use-case.md)), [policy rules](docs/product/policy-rules.md), [engines](docs/product/engines.md), [deploy & configuration](docs/product/deploy.md), [test strategy](docs/product/test-strategy.md), and [try-it guide](docs/product/try-it.md). |
| [`infra/`](infra/) | Declarative Hub & GCP setup scripts ([`infra/setup-scion.sh`](infra/setup-scion.sh), [`infra/render-templates.py`](infra/render-templates.py), [`infra/verify.sh`](infra/verify.sh)). |
| [`REVIEW.md`](REVIEW.md) / [`DEPLOYMENT.md`](DEPLOYMENT.md) | Review and deployment logs maintained by `reviewer` and `deployer`. |

## Application quickstart

> **Product Owner & Testers:** Follow the **[Try-It Guide](docs/product/try-it.md)** to test the hosted demo behind IAP or run the guided scenario presets locally.
>
> 🚀 **Live Hosted Demo (IAP):** **[https://acme-irrops-demo-vdhcml5ceq-uc.a.run.app](https://acme-irrops-demo-vdhcml5ceq-uc.a.run.app)** (Console: `/console`, WhatsApp: `/whatsapp`). Sign in with an account from an authorized Google Workspace domain (`WEB_DOMAINS` in `infra/factory.local.env`).

Run the full multi-agent application locally with Docker Compose:

```bash
# Generate per-caller tokens and launch all services:
export ACME_SIM_TOKENS="customer=$(openssl rand -hex 24),sales=$(openssl rand -hex 24),test-admin=$(openssl rand -hex 24)"
export ACME_SALES_TOKEN=$(echo "$ACME_SIM_TOKENS" | sed -n 's/.*sales=\([^,]*\).*/\1/p')
docker compose -f app/compose.yaml up --build
```

Then open:
- **Operator Console:** `http://localhost:8080/console`
- **WhatsApp Passenger Chat:** `http://localhost:8080/whatsapp`

### Runtime configuration, Compose topology & engine state

See the **[Configuration reference](docs/product/deploy.md#configuration-reference)** for every `ACME_*` and `LLM_*` variable:
- **Fail-closed credentials & LLM bounds:** `ACME_SIM_TOKENS` (secret, set at deploy; random per-process in standalone `acme-sim`, required in Compose) maps callers to allowlists (`sales` can only reach `inventory` MCP, `403` elsewhere). `ACME_SALES_TOKEN` (secret, set at deploy; no default) is required in local mode: `acme-customer` and `acme-sales` refuse to start without it, and `SalesRouteGuard` denies any Sales-credentialed call outside `POST /a2a` with `403` (#230). `LLM_MODE` defaults to `fake` ($0, offline); `vertex` requires `ACME_GENAI_LOCATION=global` and ADC (refuses API keys or regional endpoints), and `LLM_TIMEOUT_SECONDS` (default `8.0`s) cancels slow model calls so compose falls back to deterministic templates and parse fails closed to `Other` (#231).
- **Compose network split (#265):** `app/compose.yaml` isolates services across three networks: `web-net` (`10.89.1.0/24`), internal `a2a-net` (`10.89.2.0/24`), and internal `sim-net`. `customer-runtime` binds its passenger API (`ACME_CUSTOMER_HOST=10.89.1.2`, port `8800`) on `web-net` only and its A2A-only listener (`ACME_A2A_HOST=10.89.2.2`, `ACME_A2A_PORT=8802`) on `a2a-net` only, so `sales-agent` (`a2a-net`, `sim-net`) has no route to port `8800` or the browser network.
- **Background loop, read-only GETs & engine state model (#200, #228, [ADR 0103](docs/decisions/0103-stateless-engine-scenarios.md)):** In local HTTP mode, `ACME_CUSTOMER_LOOP` (default `1`, `0` disables) runs the 1 s background OCC poll, proactive WhatsApp outreach, and no-offer service closure loop, keeping all HTTP `GET` endpoints strictly read-only. On [Vertex AI Agent Engine](docs/product/engines.md), workers within a container share SQLite state via `ACME_STATE_DB=/tmp/acme-state/state.db` (`max_instances=1`), and `EngineBackend` sends `{preset, step, tape}` on every scenario call so an unseeded or restarted container deterministically rebuilds state by offline replay and replays recorded LLM answers from the `RecordingLlm`/`TapeLlm` replay tape (degrading to `FakeLlm` on `tape_miss`).

## Scion Software Factory Configuration

Everything needed to configure or reproduce the two-project factory is in **[`docs/scion/`](docs/scion/README.md)**:

1. **[`docs/scion/README.md`](docs/scion/README.md)** — The files you configure (`infra/factory.env` + gitignored `infra/factory.local.env`, `infra/deploy-config.json`, `AGENTS.md`) and 3-command quickstart.
2. **[`docs/scion/01-scion-and-gcp-setup.md`](docs/scion/01-scion-and-gcp-setup.md)** — Two-project Hub setup (`duboc-acme-airlines` & `acme-airlines-eval`) via [`infra/setup-scion.sh`](infra/setup-scion.sh), template rendering ([`infra/render-templates.py`](infra/render-templates.py)), and the 4-step IAP domain checklist (`WEB_DOMAINS`).
3. **[`docs/scion/02-agents-and-heartbeat.md`](docs/scion/02-agents-and-heartbeat.md)** — The 10 role templates across the Build & Eval projects, the 15-minute `HEARTBEAT` (`*/15 * * * *`), and the 6-step GitHub issue protocol (`Plan -> Delegate -> Review -> Commit Slot -> Deploy -> PO Accept`).
4. **[`docs/scion/03-verification-and-eval.md`](docs/scion/03-verification-and-eval.md)** — Infrastructure checks (`infra/verify.sh all` / `e2e`) and the 5-layer product evaluation methodology.

## Verify the factory

```bash
infra/verify.sh all
```
