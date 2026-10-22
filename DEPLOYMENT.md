# Deployment Log (`DEPLOYMENT.md`)

Dated release verification entries appended by `deployer` (`duboc-acme-airlines` build factory) per the [Scion agent protocol](docs/scion/02-agents-and-heartbeat.md).

## 2026-10-15 main 9b7d0e9ec51cb628e11503054eace370edc3514c (deployed by CI, verified by deployer)
- Revision `acme-irrops-demo-00227-h4f`, image `irrops-demo:9b7d0e9...`, URL https://acme-irrops-demo-vdhcml5ceq-uc.a.run.app, `/api/healthz` ok, engine agent-engine. Engines updated by Agent Engine Deploy run 38811822081.
- #372 upgrade-race 6 steps 200, done=true. #345 round trip ok (AC1240 seat 14A, RESOLVED). #305 SSE closed at 62.1 s. #380/#381 charged 135.00, upgrade_confirmed. #379 list ok.
- #337: 3x `Failed to create metric descriptor` for `workload.googleapis.com/acme.http.server.errors`, DEADLINE_EXCEEDED.

## 2026-10-16 live proof #1: main 559003c (deployed by CI, verified by deployer)
- Cloud Run `acme-irrops-demo-00245-z2q`, image `irrops-demo:559003c7cc0471a71c83714ae1a78617d778751f`; Agent Engine Deploy 559003c succeeded (Customer engine `19782688084852736`, Sales engine `1985603925432074240`). CI rolled later revisions (00247 at 72e85a8) during the session.
- Checks, 00:04-00:13Z: #392 offer_proposed/offer_sent = 1; #322 manual ack after replan gives an offer, stable over 4 reads; #325 open needs_human gives sales_suppressed `open_request_pending`; #396/#324 duty_of_care_assessed rule_reasons match policy-rules.md; #414 connection margins 135/10 min feasible; #416 verification all true (zero-bag case not measured); #395 outreach `notification_sent` fired on a poll at +7.4 min (10 min closure not measured: engine redeploy wiped the state).

## 2026-10-16 live proof #2: engines 9e2a9de, Cloud Run 00271-8k2 (dc91dae)
- Cloud Run `acme-irrops-demo-00271-8k2`, image `irrops-demo:dc91dae0decb87afde204d5505e2d59468c8c0e6`; engines `19782688084852736` (acme-customer-agent) and `1985603925432074240` (acme-sales-agent) from Agent Engine Deploy 9e2a9de. CI rolled revisions 00269 -> 00271 during the run.
- Checks, 01:27-01:35Z: #400 Cloud Trace spans carry `service.name` (customer trace `00deb03e287c94dd963e69312d565ea5`, sales trace `02a572f82d073741d6308e82999bceaf`; `acme-irrops-demo` exports no traces); #429 `ACME_DIAG rebuild` events with `tape_miss 0`, `tape_discarded 0`; #430 hotel-unavailable sends no hotel_voucher (no message at all); #440 engine `diagnostics` returns `llm_mode: "vertex"` for both engines; #431/#432 bundle `index-DbUOazLp.js`.

## 2026-10-16 live proof #3/#4: engines 38f37e6, Cloud Run 00294 (16dce4b)
- Cloud Run `acme-irrops-demo-00294-jbx`, image `irrops-demo:16dce4b...`; same two engines from Agent Engine Deploy 38f37e6 (merge freeze, no change during 02:34-02:44Z).
- Checks: #446 upgrade-race offer 135.00 (standard 290.00); #443 bundle `index-BPOXPCyY.js`; #444 AC1240 cancellation reopens the journey (RESOLVING, RESOLVED, RESOLVING, RESOLVED), no duplicate hotel/transfers/voucher, net charge 0.00; #445 decided offers close the journey (retry/rollback not reachable live); #455 scripted accept after a blocked offer returns 409 `no_offer_sent`; #438 no rebuild events in clean runs (not measured).

## 2026-10-16 live agent-engine proof and #466: engines d67f606, Cloud Run 00299 (d67f606)
- Cloud Run `acme-irrops-demo-00299-8jx`, image `irrops-demo:d67f60600a7435f3b9d66eebb22bff8131b06e4c`; engines `19782688084852736` and `1985603925432074240` from Agent Engine Deploy d67f606.
- `tests/live/test_agent_engine_traces.py -m live`: forced rebuild PASS (happy-path, engine 378.6 ms < 600 ms budget, client 527 ms, tape_miss 0, tape_discarded 0; an earlier run on the previous engines gave 426.6 ms); trace run PASS (1 passed, 580 s, 23 turns over 6 presets with trace ids). #466: reset + start + step 1 back to back fires the cancellation for 4 presets (4/4).

## 2026-10-19 #500 quiet hours use origin airport local zone: Cloud Run 00343 (8173fd2)
- Cloud Run `acme-irrops-demo-00343-r7f` (previous `00340-ljt`), image `irrops-demo:8173fd22d93dc8f66708784af6477648507f2789`, URL https://acme-irrops-demo-vdhcml5ceq-uc.a.run.app. Deployed by the push workflows IRROPS Demo Deploy (run 39306069637, success) and Agent Engine Deploy (run 39306069690, success); PR #505.
- Checks via IAP: `/api/healthz` 200 `{"status":"ok","engine":"agent-engine"}`; `/console` 200; `/whatsapp` 200. Quiet-hours behaviour on the live service: not measured. Offline pytest from a clean export: not run.

## 2026-10-22 #575 SalesRouteGuard fails closed: Cloud Run 00362 (e04f118)
- Cloud Run `acme-irrops-demo-00362-4xb` (previous `00359-xtg`), image `irrops-demo:e04f1189ba978f43ebcb9fa50a40fa7a0543b34b`; PR #580. Push workflows IRROPS Demo Deploy 39797369137 and Agent Engine Deploy 39797369158, both success.
- Checks via IAP: `/api/healthz` 200 (engine agent-engine); happy-path 5/5 steps, journey CLOSED, seat 2A, charged 135.00, Sales offer delivered; unauthenticated `/a2a` 401 from IAP; with IAP token and no Sales bearer `/a2a` 404 (no HTTP `/a2a` in agent-engine mode; Sales goes through the engine `a2a_skill`; engines carry no `ACME_SALES_TOKEN`), so the 403 guard is not exercised on the hosted demo (not measured live).

## 2026-10-22 #574 reason max_length and #584 certifi: Cloud Run 00368 (95ea023)
- Cloud Run `acme-irrops-demo-00368-s5d` (previous `00362-4xb`), image `irrops-demo:95ea023decd029d66232a2a75fd6b30386b7928d`; PRs #583 and #585. IRROPS Demo Deploy 39804243233 success. Agent Engine Deploy 39804242965: first attempt failed on the Sales engine update ("500 Build failed", cause not determined; the Customer engine had already updated), the failed job was re-run once and succeeded.
- Checks via IAP: `/api/healthz` 200 (engine agent-engine); happy-path 5/5, CLOSED, seat 2A, charged 135.00; `POST /api/scenario/trigger-cancellation` with a 1001-char reason 422 (`string_too_long`, at most 1000), with a 1000-char reason 200 (`J-331d02c0`); reset 200.
- certifi: `app/backend/uv.lock` at 95ea023 pins 2026.10.19; the version inside the deployed image was not measured.
