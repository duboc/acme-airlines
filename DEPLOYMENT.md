# Deployment Log (`DEPLOYMENT.md`)

Dated release verification entries appended by `deployer` (`duboc-acme-airlines` build factory) per the [Scion agent protocol](docs/scion/02-agents-and-heartbeat.md).

| Date (UTC) | Issue | Commit SHA | Cloud Run Revision | Live Verification Checks |
|---|---|---|---|---|

## 2026-10-15 main 9b7d0e9ec51cb628e11503054eace370edc3514c (deployed by CI, verified by deployer)
- Revision `acme-irrops-demo-00227-h4f`, image `irrops-demo:9b7d0e9...`, URL https://acme-irrops-demo-vdhcml5ceq-uc.a.run.app, `/api/healthz` ok, engine agent-engine. Engines updated by Agent Engine Deploy run 38811822081.
- #372 upgrade-race 6 steps 200, done=true. #345 round trip ok (AC1240 seat 14A, RESOLVED). #305 SSE closed at 62.1 s. #380/#381 charged 135.00, upgrade_confirmed. #379 list ok.
- #337: 3x `Failed to create metric descriptor` for `workload.googleapis.com/acme.http.server.errors`, DEADLINE_EXCEEDED.
