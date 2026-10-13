# 0103. Scenario state on Agent Engine is per container: rebuild it by deterministic replay

Status: accepted (issue #266)

## Context
Demo scenario state (world, journeys, WhatsApp transcript, scenario cursor) lives in the Customer
engine. #200 made it shared across the 9 agent processes of one container through SQLite in the
container's `/tmp`. The #266 diagnostics (run 38474100288) showed exactly that working: all 17 turns on
one container, spread over 4 processes, snapshot versions 1 to 36, no failure. So the per-process
hypothesis is refuted and the remaining one is **per container**: `/tmp` is container-local, so a call
routed to a second replica, or arriving after a restart or recycle, finds no scenario
(`no scenario is started`), at a turn that moves from run to run. One green run does not close an
intermittent bug.

## Decision
**The caller says where the scenario is on every call; a container that is not there rebuilds the
state by deterministic replay.** No new external service.
- `preset` and `step` are optional parameters of the scenario ops (`scenario_state`, `scenario_step`)
  and of the reads and phone actions that depend on its state (`journeys_list`, `journey_detail`,
  `journey_events`, `whatsapp_messages`, `whatsapp_reply`, `whatsapp_action`). The demo backend
  (`EngineBackend`, hence also the live proof) sends them whenever a scenario is active. Callers that
  send nothing behave as before.
- If the local state is not at `(preset, step)` (no scenario, another preset, another step), the
  engine starts the preset and replays steps `0..step-1` from the seeded mocks, inside the same
  cross-process lock, then runs the requested operation. The mocks, the fixed `Clock`, the guardrail
  and the policy code are deterministic, so the rebuilt state equals the lost one (a test compares
  every preset against the single-process run).
- **Replay is invisible outside the container.** It recreates state the lost container already
  reported, so it emits no spans and no metrics (`acme.replay`), and it uses the offline model
  (`FakeLlm`) instead of the deployed one: deterministic, free, and no extra model calls. Consequence:
  the wording of a message the passenger saw may differ after a rebuild when `LLM_MODE=vertex`; the
  facts, state and guardrail outcomes do not. Offers are re-created by the in-process radar at the
  same step, so a later real Sales proposal is just a duplicate the guardrail rejects
  (`offer_already_made`), as it already does.
- **Visible in the diagnostics.** Each rebuild logs `ACME_DIAG {"event": "rebuild", preset, from_step,
  to_step, reason, ms, proc, container, pid}`, so the next live proof shows whether cross-container
  routing happens and how often.
- Sales' own A2A calls to the Customer engine carry no scenario context (the Sales engine does not know
  the step): a Sales tick that lands on a container with stale state sees stale journeys and proposes
  nothing, or a duplicate the guardrail rejects. The next Customer call rebuilds. Accepted.

## Alternatives
- **External store (Firestore, Memorystore, GCS, Cloud SQL).** Durable and exact (original wording
  preserved, no replay cost) but a new service, IAM, cost and an operator action for a demo whose state
  is seeded and deterministic. Rejected; revisit if scenarios stop being deterministic.
- **Pin to one replica / one container.** `max_instances=1` already holds, but restarts and recycles
  still lose `/tmp`, and Agent Engine offers no affinity setting.
- **Keep the state and tell the user to restart the scenario.** The failure moves between turns; that
  is the bug.

## Consequences
- A rebuild costs the replay time (the five-step presets take well under a second offline); it only
  happens after a container change.
- Replicas are independent mocks kept consistent by the caller's cursor, not by shared storage.
- The cost model is O(steps) per rebuild, bounded by the presets' length.
