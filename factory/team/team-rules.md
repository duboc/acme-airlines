# Project Context (`duboc/acme-airlines` on Scion)

You run as a [`scion-frontiers/agent-team`](https://github.com/scion-frontiers/agent-team) agent on the `duboc-acme-airlines` Scion Hub project (repository `duboc/acme-airlines`, product owner `admin@duboc.altostrat.com`).

1. **Read [`AGENTS.md`](AGENTS.md) (`CLAUDE.md`) first** for build/test commands, "Two Goals, One Guardrail" invariants, and PR rules (`≤ 400` lines, no secrets, `LLM_MODE=fake` in tests, Vertex `global` only).
2. **Shared scratchpad (`/scion-volumes/scratchpad/`):** Mounted in every container (`scratchpad` shared-dir). Follow `agent-state-continuity` and `artifact-durability`: store `<agent-name>-state.md` and briefs under `/scion-volumes/scratchpad/projects/duboc-acme-airlines/`.
3. **6-Template Roster (`scion-frontiers/agent-team`):**
   - **`coordinator`** (`claude` / `claude-opus-5-5`): Single persistent project coordinator (`acme-lead`).
   - **`eng-manager`** (`claude` / `claude-opus-5-5`): Multi-phase epic orchestrator, spawned ephemerally and deleted when the epic merges.
   - **`architect`** (`antigravity` / `Gemini 4 Argon (High)` → `gemini-4-argon`): Produces design docs before implementation, deleted when done.
   - **`developer`** (`claude` / `claude-sonnet-5-5` + mounted `impeccable` UI skill): Implements backend/frontend features and tests on a branch, deleted after PR merges.
   - **`code-reviewer`** (`antigravity` / `Gemini 4 Argon (High)` → `gemini-4-argon`): Evaluates PRs across correctness, architecture, security, and tests, deleted after review.
   - **`test-engineer`** (`antigravity` / `Gemini 3.8 Flash (Medium)` → `gemini-3.8-flash`): Designs test suites, scenario e2e checks, and guardrail verification, deleted when done.
4. **GitHub Issues & PR review gate:**
   - Track task state on the GitHub issue (`[agent] STATUS/DONE/NEXT/BLOCKER`) and notify `acme-lead` via `scion message acme-lead`.
   - `code-reviewer` posts `gh pr review <n> --comment --body-file <file>` starting with `VERDICT: APPROVE` or `VERDICT: REQUEST CHANGES` plus the 8-item checklist in `AGENTS.md`.
   - Only `acme-lead` (or an active `eng-manager`) merges (`gh pr merge --squash --delete-branch` once `VERDICT: APPROVE` and required checks `backend`, `engine-smoke`, `frontend`, `pr-size` are green) and immediately deletes finished worker agents (`scion delete <agent>`).
   - Act only on issues, PRs, and comments authored by `duboc`.
