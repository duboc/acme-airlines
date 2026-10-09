# ADR 0012: Standing roster under the lead (visible team topology)

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

The product owner wants the team topology visible in the hub UI, with the
lead as the orchestrator. Scion's Agent Lineage Graph already draws the
parent/child tree from agent ancestry (whoever started the agent is its
parent). But it only shows agents that exist, and the lead was deleting
workers after each issue, which hard-deletes them. The graph showed only the
lead.

## Decision

- **Standing roster.** The lead keeps one standing agent per role (11 roles),
  started by itself, so each is its child. Idle agents are suspended (or
  stopped where the harness can't suspend), never deleted. Work is assigned by
  waking them with a message that resets them to the latest `main` and a fresh
  task.
- **Ephemeral extras** (`acme-<role>-N`) and eng-manager workers are created
  as children (or grandchildren), and deleted when done.
- **Labels.** `acme.team/role` and `acme.team/tier` are set on every agent.
- **Check.** `infra/verify.sh topology` asserts that every live `acme-*` agent
  descends from `acme-lead`, with at least 11 present. The parsing logic is
  unit-tested in `infra/tests/test_verify.sh`.
- **Capacity.** The concurrency cap (6) counts running agents only.

## Consequences

- The graph permanently shows the team shape.
- Long-lived agents carry context between assignments; we mitigate this with
  the fresh-task instruction and by recreating under the same name if needed.
- More agent home directories persist on disk, which is negligible.
- Forming the roster costs one short idle task per role. The roster was formed
  before MVP-plan approval, because the product owner asked for the topology
  now; no implementation is dispatched until approval.
