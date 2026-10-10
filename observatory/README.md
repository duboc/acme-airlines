# Observatory

## CI

`.github/workflows/observatory-ci.yml` runs on PRs and pushes to `main` that touch
`observatory/**` or `.github/workflows/observatory-*.yml`. The main `CI` workflow is separate.
It sets `OBSERVATORY_DATA=fake`, uses no secrets and no GCP auth, and needs only package installs.

| Job | Directory | Steps |
|-----|-----------|-------|
| `collector` | `observatory/collector` | `uv sync --frozen`, `ruff check`, `ruff format --check`, `pytest` |
| `dashboard-api` | `observatory/dashboard` | same as `collector` |
| `dashboard-web` | `observatory/dashboard/web` | `npm ci`, `npm run lint`, `npm test`, `npm run build` |
| `docker-build` | `collector`, `dashboard` | `docker build` (no push) |

A job whose project (or Dockerfile) does not exist yet logs `SKIPPED: ...` and passes.
Once the project exists, every step is enforced.
