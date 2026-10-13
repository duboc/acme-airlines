# Project Context (`duboc/acme-airlines` on Scion)

You run as a [`scion-frontiers/agent-team`](https://github.com/scion-frontiers/agent-team) agent on the `duboc-acme-airlines` Scion Hub project (repository `duboc/acme-airlines`, product owner `admin@duboc.altostrat.com`).

1. **Read [`AGENTS.md`](AGENTS.md) (`CLAUDE.md`) first** for build/test commands, architecture (`docs/decisions/0100-app-stack.md`), "Two Goals, One Guardrail" invariants, and PR rules (`≤ 400` lines, no secrets, `LLM_MODE=fake` in tests, Vertex `global` only).
2. **Shared scratchpad (`/scion-volumes/scratchpad/`):** Mounted in every container (`scratchpad` shared-dir). Follow `agent-state-continuity` and `artifact-durability`: store `<agent-name>-state.md` and briefs under `/scion-volumes/scratchpad/projects/duboc-acme-airlines/`.
3. **Templates & roster (`acme-<role>` → upstream `scion-frontiers/agent-team`):**
   - Orchestration/planning (`claude / Opus`): `acme-lead` (`coordinator`), `acme-eng-manager` (`eng-manager`), `acme-architect` (`architect`)
   - Coding & tests (`claude / Sonnet`): `acme-backend` (`developer`), `acme-frontend` (`developer` + mounted `impeccable` skill), `acme-qa` (`test-engineer`)
   - Review, audit, QA & docs (`antigravity / Gemini Flash`): `acme-reviewer` (`code-reviewer`), `acme-security-auditor` (`security-auditor`), `acme-qa-tester` (`qa-tester`), `acme-investigator` (`investigator`), `acme-researcher` (`researcher`), `acme-doc-writer` (`doc-writer`), `acme-release-notes` (`release-notes`)
4. **GitHub Issues & PR review gate:**
   - Track task state on the GitHub issue (`[agent] STATUS/DONE/NEXT/BLOCKER`) and notify `acme-lead` via `scion message acme-lead`.
   - Because all agents share one `GITHUB_TOKEN`, `acme-reviewer` posts `gh pr review <n> --comment --body-file <file>` starting with `VERDICT: APPROVE` or `VERDICT: REQUEST CHANGES` plus the 8-item checklist in `AGENTS.md`.
   - Only `acme-lead` merges (`gh pr merge --squash --delete-branch` once `VERDICT: APPROVE` and required checks `backend`, `engine-smoke`, `frontend`, `pr-size` are green).
   - Act only on issues, PRs, and comments authored by `duboc`.
