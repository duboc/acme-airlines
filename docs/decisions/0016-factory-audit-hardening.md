# ADR 0016: Factory Audit Hardening — Least-Privilege Agent Identity, Wake-on-Change Watchdog, and Process Gates

- **Date:** 2026-10-13
- **Status:** Accepted
- **Deciders:** Product Owner (`duboc`), Operator

## Context

After 72 hours of continuous autonomous operation (86 merged PRs across 103 issues), a four-part audit of the `acme-airlines` Scion software factory (security & IAM, Scion best-practices gaps, autonomy/process/cost metrics, and playbook reproducibility) surfaced four systemic risks:

1. **Prompt-cache expiry & idle wake cost:** `acme-lead` (Claude Opus) ran a single continuous 3-day session and was woken every 30 minutes by `acme-factory-tick`. Because Anthropic's default prompt-cache TTL is 5 minutes, every 30-minute wake re-wrote `acme-lead`'s accumulated context from scratch (`103M` `cache_write_input` tokens in 72h, ~63% of total model spend).
2. **Silent coordinator stall risk:** No external health check watched `acme-lead`'s container or Hub activity state; when `acme-lead` stalled once, the factory halted until manually noticed.
3. **Excessive container and deployer IAM privileges:**
   - Single-node VM `deploy.sh` defaulted to `default_gcp_identity_mode: passthrough` and granted `roles/iam.serviceAccountAdmin` to the VM service account (`scion-hub-acme@`), allowing any agent container via the GCE metadata server to mint tokens or keys for other service accounts in the project.
   - `acme-observatory-deployer` held project-wide `roles/run.developer`, which could modify `scion-hub-acme-iap-proxy` or `acme-budget-guard`.
   - `default_user_role` in Hub `settings.yaml` was `member`, allowing any `@google.com` user passing IAP to create agents or read Hub state.
4. **Unenforced PR size and review checklist:** 45% of merged PRs exceeded the 400-line team rule because it was prose-only, and making the repository public to restore GitHub Actions runners required protection against untrusted issue/PR authors and direct pushes to `main`.

## Decisions

### 1. Wake-on-Change Watchdog + 6-Hour Safety-Net TICK + Daily Lead Reincarnation
- Deploy Cloud Run service `acme-factory-watchdog` (`infra/watchdog/`, `infra/76-watchdog.sh`), triggered every hour (`0 * * * *`) by Cloud Scheduler (`acme-factory-watchdog`):
  - Computes a SHA-256 fingerprint over open GitHub issues (excluding `"🏭 Factory status"`), open PRs, recent `main` workflow runs, and non-lead agent `(slug, phase, activity)` states.
  - Wakes `acme-lead` only when `acme-lead` is idle (`blocked`/`waiting_for_input`/`completed`) AND the fingerprint changed since the last check (rate-limited to at most 1 wake per 55 minutes).
  - Never resumes a `suspended` lead (preserving intentional pauses by `infra/60-factory.sh pause` or `acme-budget-guard`).
  - Self-heals an unhealthy lead (`error`/`stopped` phase → `scion resume`; `stalled`/`crashed`/`offline`/`limits_exceeded` activity → recovery message first, then `scion reincarnate` + `alert_po=true` on the next hourly check).
- Move the Hub recurring schedule `acme-factory-tick` from `*/30 * * * *` to `0 */6 * * *` (`TICK_CRON` in `infra/factory.env`) as a safety net.
- Schedule daily reincarnation of `acme-lead` at `04:00 UTC` (`acme-lead-reincarnate` → `POST /reincarnate`) using a structured handoff built from `"🏭 Factory status"` so `acme-lead` starts each day with a compact context.
- Wire a Cloud Monitoring log-based alert policy (`acme-factory-watchdog-po-alert`) on `jsonPayload.alert_po=true` to email `ADMIN_EMAIL`.

### 2. Dedicated Agent Service Account (`assign` Mode) & Least-Privilege IAM (`infra/25-agent-identity.sh`)
- Set `"hub_sa_minting": false` in `infra/deploy-config.json` and remove `roles/iam.serviceAccountAdmin` from `scion-hub-acme@` at project level.
- Create dedicated low-privilege service account `acme-agent@acme-airlines-factory.iam.gserviceaccount.com` holding only:
  - `roles/aiplatform.user`
  - `roles/logging.logWriter`
  - `roles/monitoring.metricWriter`
  - `roles/cloudtrace.agent`
- Grant `scion-hub-acme@` `roles/iam.serviceAccountTokenCreator` on `acme-agent@`, register and verify `acme-agent@` on Hub project `duboc-acme-airlines`, set the project-default GCP identity to `assign` (`acme-agent@`), set Hub `default_gcp_identity_mode: block`, and migrate all standing agents and `acme-lead` to `assign` mode.
- Replace project-wide `roles/run.developer` on `acme-observatory-deployer@` with project-level `roles/run.viewer` (for LRO polling) plus resource-level `roles/run.developer` on `acme-observatory`, `acme-irrops-demo`, and job `acme-observatory-collector`.
- Set `default_user_role: viewer` in Hub `settings.yaml` (`infra/50-web-access.sh`).

### 3. Observability, Backups, and Process Guardrails
- Enable Scion Cloud Monitoring telemetry export (`telemetry.cloud` in Hub `settings.yaml`) for per-agent token and API-call attribution (`gen_ai.api.calls`, `scion.usage.tokens`).
- Add `max_model_calls: 3 * max_turns` to all 13 `acme-*` templates (`infra/40-build-team-templates.sh`) and push them to the Hub (`infra/45-push-templates.sh`).
- Attach a daily boot-disk snapshot policy (`7d` retention, `03:00 UTC`) to `scion-hub-acme` (`infra/75-backups.sh`).
- Enforce branch protection on `main` (`enforce_admins=true`, required checks `backend`, `engine-smoke`, `frontend`, `pr-size`), add `.github/workflows/pr-size.yml` (400-line gate, bypassable via `size:exception`), require an 8-item verification checklist in `acme-reviewer` verdicts, and restrict agent actions on the public repo to issues/PRs/comments authored by `duboc` (`collaborators_only` interaction limit + team rule #9).

## Consequences

- **Cost:** Eliminates 48 daily blind Opus wake-ups and bounds context growth via daily reincarnation; raises monthly budget guardrail to `$2,000` (`BUDGET_USD="2000"`) while keeping automatic agent suspension at 100%.
- **Security:** Compromising an agent container no longer exposes `scion-hub-acme@` or `iam.serviceAccountAdmin`; compromising GitHub Actions deployer credentials cannot touch the Hub IAP proxy, budget guard, or watchdog.
- **Verification:** `infra/verify.sh` enforces `webaccess`, `backups`, `watchdog`, `identity`, and `telemetry` as part of `verify.sh all`.
