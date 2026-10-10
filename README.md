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
| [`infra/`](infra/) | Scripts and config that create and operate the factory. |
| [`docs/setup/`](docs/setup/) | How the factory was built, step by step, and how to run it. |
| [`docs/decisions/`](docs/decisions/) | Architecture decision records (app stack: [ADR 0100](docs/decisions/0100-app-stack.md)). |
| [`docs/product/`](docs/product/) | Product brief (IRROPS use case), [MVP plan](docs/product/mvp-plan.md), [try-it guide](docs/product/try-it.md), and [demo script](docs/product/demo-script.md). |
| [`docs/factory/`](docs/factory/) | How to work with the agent team; smoke-test records. |
| [`factory/team/`](factory/team/) | Source for the agent team's role templates. |
| [`factory/templates/`](factory/templates/) | Generated Scion role templates (`acme-*`). |
| [`docs/superpowers/`](docs/superpowers/) | Design spec and implementation plan for the factory. |

## Application quickstart

> **Product Owner & Testers:** Follow the **[Try-It Guide](docs/product/try-it.md)** to test the hosted demo behind IAP or run the guided scenario presets locally.

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

For the step-by-step narrative demo walkthrough, follow the [Demo Script](docs/product/demo-script.md). For guided testing of scenario variants and edge cases, see the [Try-It Guide](docs/product/try-it.md).

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

## Start here

1. [Overview](docs/setup/00-overview.md) — architecture, resources, cost.
2. [Runbook](docs/setup/01-runbook.md) — every command we ran, in order.
3. [Operations](docs/setup/02-operations.md) — access, logs, stop/start, upgrades.
4. [Troubleshooting](docs/setup/03-troubleshooting.md)
5. [Teardown](docs/setup/04-teardown.md)
6. [Working with the agent team](docs/factory/team.md)

## Verify the factory

```bash
infra/verify.sh all
```
