# ADR 0103: Stateless engine scenarios (#266, amended by #279)

Status: accepted (restored in #279; the original file was missing from the repo).

## Context
Agent Engine runs several containers per engine and routes each call to any of them. Scenario state
lives in container-local `/tmp`, so a call that lands on another container, or after a restart,
found no scenario and failed.

## Decision
The caller (`EngineBackend`, so the live proof too) sends `preset` and `step` with every scenario op
and every state-dependent read or phone action. An engine that is not at that point rebuilds the
scenario by deterministic replay of steps `0..step-1` from the seeded mocks (`_ensure_scenario`):
offline model, no spans, no metrics, and an `ACME_DIAG rebuild` line with the reason, the steps, the
duration and `tape_miss`. Callers that send no context behave as before.

Arguments arrive through a protobuf Struct, so integers are doubles (`5.0`): `integral()` accepts
whole numbers and rejects everything else (#279).

## Amendment: the replay tape (#279)
Replaying with the fake model made the passenger's history read differently after a rebuild: the
deployed engines run `LLM_MODE=vertex`, and the text of resolution and offer messages (and the parse
of a passenger reply) is the model's answer. Found by the live proof: state and journeys matched,
messages did not.

The Customer engine therefore records every LLM answer in order (`RecordingLlm`: composed text or
parsed request, or a failure) and returns it as `tape` in the scenario state. `EngineBackend` keeps
the tape next to `preset`/`step` and sends it back on every call. A rebuild replays it (`TapeLlm`),
per method and in order; a missing or mismatching entry falls back to the fake model and is counted
(`tape_miss`, a number only). A new scenario starts a new tape.

- The tape is untrusted input: a list of at most 32 entries, texts at most 2000 characters, parse
  entries validated against the parse schema. A malformed tape is ignored as a whole.
- The tape holds passenger text. It travels only between the Customer engine and its caller; it is
  never logged, put in `ACME_DIAG`, spans or metrics, and never sent to Sales or returned by the API.
- Callers without a tape (and older engines) behave as before.

## Known gap
The offer text is composed on whichever container serves the Sales tick (`a2a_skill`), and the tape
the caller holds is the one returned by the next Customer call. If that call lands on another
container, the offer text is not on the tape: it is replayed with the fake model and `tape_miss` is
greater than 0. State, journeys and the absence of duplicates are unaffected.

## Consequences
Rebuild cost is about 300 ms per replayed step offline (see #280 for the latency budget, which is
not decided here).
