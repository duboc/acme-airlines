#!/usr/bin/env python3
"""Fail the nightly mutation run when any mutant survived; write a Markdown summary.

Usage: mutation_gate.py <mutmut-cicd-stats.json> <mutmut-results.txt> [summary.md]
(`mutmut export-cicd-stats` and `mutmut results`). Survived, untested ("no tests"), suspicious
and crashed mutants fail the run and are listed. Timeouts are reported but do not fail: they are
mutants that hung, which is detection, as long as the timeout limit is generous (see
`timeout_constant` in app/backend/pyproject.toml).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FAILING = ("survived", "no_tests", "suspicious", "segfault")
LISTED = re.compile(r"^\s*(?P<name>\S+): (?P<status>survived|no tests|suspicious|segfault)\s*$")


def evaluate(stats: dict[str, int], results_text: str) -> tuple[int, str]:
    total = stats.get("total", 0)
    if total == 0:
        return 1, "## Mutation testing\n\n**FAILED**: no mutants were generated or run.\n"
    bad = {k: stats.get(k, 0) for k in FAILING if stats.get(k, 0)}
    lines = ["## Mutation testing of `acme` (policy + governance paths, #281)", ""]
    lines.append(f"{stats.get('killed', 0)} of {total} mutants killed"
                 + (f", {stats['timeout']} timed out" if stats.get("timeout") else "") + ".")  # fmt: skip
    if not bad:
        return 0, "\n".join(lines) + "\n\nNo surviving mutants.\n"
    lines += ["", "**FAILED**: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in bad.items()),
              "", "| Mutant | Status |", "|---|---|"]  # fmt: skip
    for line in results_text.splitlines():
        m = LISTED.match(line)
        if m:
            lines.append(f"| `{m['name']}` | {m['status']} |")
    lines += ["", "Inspect one with `cd app/backend && uv run mutmut run && "
              "uv run mutmut show <mutant>`, then add a test that fails for it (or, for a "
              "provably equivalent mutant, mark the line `# pragma: no mutate` with a reason)."]  # fmt: skip
    return 1, "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    stats = json.loads(Path(argv[0]).read_text())
    code, markdown = evaluate(stats, Path(argv[1]).read_text())
    if len(argv) > 2:
        with Path(argv[2]).open("a") as out:
            out.write(markdown)
    print(markdown)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
