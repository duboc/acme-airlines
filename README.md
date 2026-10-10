# acme-airlines

Home of the **acme-airlines** application and of the **software factory** that
builds it: a [Scion](https://github.com/GoogleCloudPlatform/scion) Hub running on
a single GCE VM, where LLM coding agents (Gemini via Antigravity, and Claude —
both on Vertex AI) are dispatched to work on this repository and deliver
changes as pull requests.

## Layout

| Path | What |
|------|------|
| [`app/`](app/) | The acme-airlines application (to be built by the factory). |
| [`infra/`](infra/) | Scripts and config that create and operate the factory. |
| [`docs/setup/`](docs/setup/) | How the factory was built, step by step, and how to run it. |
| [`docs/decisions/`](docs/decisions/) | Architecture decision records (app stack: [ADR 0100](docs/decisions/0100-app-stack.md)). |
| [`docs/product/`](docs/product/) | Product brief (IRROPS use case) and [MVP plan](docs/product/mvp-plan.md). |
| [`docs/factory/`](docs/factory/) | How to work with the agent team; smoke-test records. |
| [`factory/team/`](factory/team/) | Source for the agent team's role templates. |
| [`factory/templates/`](factory/templates/) | Generated Scion role templates (`acme-*`). |
| [`docs/superpowers/`](docs/superpowers/) | Design spec and implementation plan for the factory. |

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
