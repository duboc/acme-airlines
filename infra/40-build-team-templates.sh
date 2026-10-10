#!/usr/bin/env bash
# Addendum A, Task 4: build the acme-* Scion role templates into factory/templates/.
# (Not .scion/templates/: a repo's .scion/templates are project-scope templates
#  that shadow the hub's global ones inside agent workspaces; issue #38 thread.)
#
# Each template has three parts:
#   - agents.md        = factory/team/roles/<role>.md + factory/team/team-rules.md
#                        + the upstream scion-frontiers/agent-team agents.md
#   - system-prompt.md = upstream system-prompt.md
#   - scion-agent.yaml = harness/model per role + the upstream skills list
# Upstream is pinned to AGENT_TEAM_SHA (Apache-2.0); attribution is kept in
# each generated file. Re-run after editing factory/team/*; commit the output.
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"

AGENT_TEAM_SHA="${AGENT_TEAM_SHA:-3650f1b03411116fcbf37852bfec6911db64b569}"
UP="${REPO_ROOT}/.cache/agent-team-${AGENT_TEAM_SHA:0:7}"
OUT="${REPO_ROOT}/factory/templates"
TEAM="${REPO_ROOT}/factory/team"

# role | upstream template | harness-config | model alias | description
# role | upstream | harness | model | max_duration | max_turns | extra skill URI | description
# Addendum B: Opus = coordination/planning, Sonnet = coding, Antigravity Flash = everything else.
# impeccable is vendored as a TEMPLATE-MOUNTED skill (skills/impeccable/ inside the
# template): the hub's gh:// shorthand only resolves gh://owner/repo/<name> -> skills/<name>,
# and impeccable's built skill lives at .claude/skills/impeccable (issue #38).
IMPECCABLE_SHA="d631a8827f99414d2b6daba4ef08b7f8701751d7"
IMPECCABLE="mount:impeccable"
ROLES=(
  "lead|coordinator|claude|large|||-|ACME lead / tech lead — runs the factory loop, owns the backlog, merges autonomously"
  "eng-manager|eng-manager|claude|large|4h|800|-|ACME engineering manager — runs one workstream under the lead; never merges"
  "architect|architect|claude|large|1h|200|-|ACME architect — design docs and ADRs before implementation"
  "backend|developer|claude|medium|2h|400|-|ACME backend developer — agent services, A2A/MCP mocks, APIs, tests"
  "frontend|developer|claude|medium|2h|400|${IMPECCABLE}|ACME frontend developer — ops console and passenger chat UI, designed with impeccable"
  "qa|test-engineer|claude|medium|2h|400|-|ACME QA / test engineer — scenario e2e tests, guardrail tests, CI gates"
  "reviewer|code-reviewer|antigravity|medium|45m|150|-|ACME code reviewer — reviews every PR; never pushes code"
  "security-auditor|security-auditor|antigravity|medium|45m|150|-|ACME security auditor — vulnerability, prompt-injection, PII and guardrail audits"
  "qa-tester|qa-tester|antigravity|medium|45m|150|-|ACME QA tester — exploratory/acceptance testing of the running app (Playwright)"
  "investigator|investigator|antigravity|medium|30m|100|-|ACME investigator — reproduces bugs and scopes issues before development"
  "researcher|researcher|antigravity|medium|30m|100|-|ACME researcher — cited research on regulations, protocols and APIs"
  "doc-writer|doc-writer|antigravity|medium|30m|100|-|ACME documentation writer — keeps docs aligned with code"
  "release-notes|release-notes|antigravity|medium|30m|100|-|ACME release engineer — semantic release notes from git history"
)

fetch() {  # fetch <upstream-template> <file>
  local dest="${UP}/$1/$2"
  [[ -s "${dest}" ]] && return 0
  mkdir -p "${UP}/$1"
  gh api "repos/scion-frontiers/agent-team/contents/templates/$1/$2?ref=${AGENT_TEAM_SHA}" -q .content \
    | base64 -d > "${dest}"
}

# vendor_skill impeccable <dest-skills-dir>: copy pbakaus/impeccable@IMPECCABLE_SHA's
# built Claude skill (+ LICENSE/NOTICE, Apache-2.0) into <dest>/impeccable.
vendor_skill() {
  local name="$1" dest="$2" cache="${REPO_ROOT}/.cache/impeccable-${IMPECCABLE_SHA:0:7}"
  [[ "${name}" == impeccable ]] || { echo "unknown mounted skill ${name}" >&2; return 1; }
  if [[ ! -f "${cache}/.claude/skills/impeccable/SKILL.md" ]]; then
    rm -rf "${cache}"; git init -q "${cache}"
    git -C "${cache}" remote add origin https://github.com/pbakaus/impeccable.git
    git -C "${cache}" sparse-checkout set --no-cone .claude/skills/impeccable LICENSE NOTICE.md
    git -C "${cache}" fetch -q --depth 1 origin "${IMPECCABLE_SHA}"
    git -C "${cache}" checkout -q FETCH_HEAD
  fi
  rm -rf "${dest:?}/impeccable"; mkdir -p "${dest}"
  cp -R "${cache}/.claude/skills/impeccable" "${dest}/impeccable"
  cp "${cache}/LICENSE" "${dest}/impeccable/LICENSE"
  cp "${cache}/NOTICE.md" "${dest}/impeccable/NOTICE.md"
  printf 'Vendored from https://github.com/pbakaus/impeccable/tree/%s/.claude/skills/impeccable (Apache-2.0)\nby infra/40-build-team-templates.sh. Do not edit.\n' "${IMPECCABLE_SHA}" > "${dest}/impeccable/VENDORED.txt"
}

for spec in "${ROLES[@]}"; do
  IFS='|' read -r role upstream harness model maxdur maxturns extraskill desc <<<"${spec}"
  for f in agents.md system-prompt.md scion-agent.yaml; do fetch "${upstream}" "${f}"; done
  dir="${OUT}/acme-${role}"; mkdir -p "${dir}"
  attribution="<!-- Generated by infra/40-build-team-templates.sh. Edit factory/team/*, not this file.
Upstream: https://github.com/scion-frontiers/agent-team/tree/${AGENT_TEAM_SHA}/templates/${upstream} (Apache-2.0) -->"

  { echo "${attribution}"; echo; cat "${TEAM}/roles/${role}.md"; echo; cat "${TEAM}/team-rules.md"; echo
    echo "---"; echo; echo "# Upstream role guidance (${upstream})"; echo; cat "${UP}/${upstream}/agents.md"; } \
    > "${dir}/agents.md"
  { echo "${attribution}"; echo; cat "${UP}/${upstream}/system-prompt.md"; } > "${dir}/system-prompt.md"

  {
    echo "# Generated by infra/40-build-team-templates.sh — edit the generator, not this file."
    echo 'schema_version: "1"'
    echo "description: \"${desc}\""
    echo "agent_instructions: agents.md"
    echo "system_prompt: system-prompt.md"
    echo "default_harness_config: ${harness}"
    echo "model: ${model}"
    if [[ -n "${maxdur}" ]]; then echo "max_duration: \"${maxdur}\""; fi
    if [[ -n "${maxturns}" ]]; then echo "max_turns: ${maxturns}"; fi
    # env: upstream entries (e.g. qa-tester's Playwright path) + Claude haiku override.
    upstream_env="$(awk '/^env:/{f=1;next} f&&/^[^ #]/{f=0} f&&/^  [A-Z_]+:/' "${UP}/${upstream}/scion-agent.yaml")"
    if [[ -n "${upstream_env}" || "${harness}" == "claude" ]]; then
      echo "env:"
      [[ -n "${upstream_env}" ]] && echo "${upstream_env}"
      if [[ "${harness}" == "claude" ]]; then
        echo "  # claude-haiku-4-5 (harness default) is not enabled on Vertex; 5.5 is."
        echo "  ANTHROPIC_DEFAULT_HAIKU_MODEL: claude-haiku-5-5"
      fi
    fi
    # Upstream skills list verbatim (from 'skills:' to EOF), plus an optional extra skill.
    up_skills="$(sed -n '/^skills:/,$p' "${UP}/${upstream}/scion-agent.yaml")"
    if [[ -n "${up_skills}" ]]; then echo "${up_skills}"; fi
  } > "${dir}/scion-agent.yaml"
  if [[ "${extraskill}" == mount:* ]]; then vendor_skill "${extraskill#mount:}" "${dir}/skills"; fi
  echo "built ${dir#"${REPO_ROOT}/"} (${harness}/${model}, upstream ${upstream})"
done
