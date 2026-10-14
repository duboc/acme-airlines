# acme-airlines

Home of the **acme-airlines** application and of the **software factory** that
builds it: a [Scion](https://github.com/GoogleCloudPlatform/scion) Hub running on
a single GCE VM, where LLM coding agents (Gemini via Antigravity, and Claude —
both on Vertex AI) are dispatched to work on this repository and deliver
changes as pull requests.

## Layout

| Path | What |
|------|------|
| [`app/`](app/) | The acme-airlines multi-agent application (see [`app/README.md`](app/README.md)). |
| [`docs/scion/`](docs/scion/README.md) | Concise Scion, GCP/IAP, `scion-frontiers/agent-team`, heartbeat, and eval configuration guides. |
| [`docs/scion/templates/`](docs/scion/templates/) | The 6 Scion role templates (`coordinator`, `eng-manager`, `architect`, `developer`, `code-reviewer`, `test-engineer`) from [`scion-frontiers/agent-team`](https://github.com/scion-frontiers/agent-team). |
| [`docs/product/`](docs/product/) | Product brief ([IRROPS use case](docs/product/irrops-use-case.md)), [policy rules](docs/product/policy-rules.md), [engines](docs/product/engines.md), [test strategy](docs/product/test-strategy.md), and [try-it guide](docs/product/try-it.md). |
| [`infra/`](infra/) | Scripts and config that create and operate the factory (`infra/setup-scion.sh`). |

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

For guided testing of scenario variants and edge cases, see the [Try-It Guide](docs/product/try-it.md) and [Test Strategy](docs/product/test-strategy.md).

### LLM Mode (`LLM_MODE`)

- **Offline (default, $0):** `LLM_MODE=fake` uses deterministic local fakes with zero cloud dependencies.
- **Live Vertex AI:** `LLM_MODE=vertex docker compose -f app/compose.yaml up --build` uses Gemini on Vertex AI (`location=global`, ADC only, project `acme-airlines-factory`). See [`docs/product/deploy.md`](docs/product/deploy.md).

### Running tests

```bash
# Backend lint and tests (pytest, ruff)
cd app/backend
uv sync --frozen
uv run ruff check
uv run pytest -m "not live"

# Frontend lint, tests, and build (Vitest, ESLint)
cd app/frontend
npm ci
npm run lint
npm test
npm run build
```

## Scion Software Factory Configuration

Everything needed to configure or reproduce the factory is in **[`docs/scion/`](docs/scion/README.md)**:

1. **[`docs/scion/README.md`](docs/scion/README.md)** — The files you configure (`infra/factory.env` + gitignored `infra/factory.local.env`, `infra/deploy-config.json`, `AGENTS.md`) and 3-command quickstart.
2. **[`docs/scion/01-scion-and-gcp-setup.md`](docs/scion/01-scion-and-gcp-setup.md)** — Provisioning scripts (`infra/00..95`), declarative `scion` CLI Hub setup (`infra/setup-scion.sh`), and the 4-step IAP domain checklist (`WEB_DOMAINS`).
3. **[`docs/scion/02-agents-and-heartbeat.md`](docs/scion/02-agents-and-heartbeat.md)** — Agent templates built from [`scion-frontiers/agent-team`](https://github.com/scion-frontiers/agent-team), 3-tier hierarchy, and 4-layer heartbeat (hourly `0 * * * *` watchdog, daily `04:00 UTC` reincarnation, 6h fallback tick, `$2,000` budget guard).
4. **[`docs/scion/03-verification-and-eval.md`](docs/scion/03-verification-and-eval.md)** — Infrastructure checks (`infra/verify.sh all` / `e2e`) and the 5-layer product evaluation methodology.

## Verify the factory

```bash
infra/verify.sh all
```
