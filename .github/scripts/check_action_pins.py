#!/usr/bin/env python3
"""Fail if any workflow/composite action uses a third-party action that is not SHA-pinned.

Rules for every `uses:` reference (workflows under .github/workflows, composite actions under
.github/actions):
- `owner/repo[/path]@<40 hex chars>` followed by a `# <version>` comment (e.g. `# v4.2.2`);
- `docker://image@sha256:<64 hex>`;
- local `./path` actions are exempt.
Tags and branches are mutable: a compromised tag would run attacker code with this repo's
`id-token: write` deploy rights. Dependabot (see .github/dependabot.yml) bumps pins weekly.

Usage: python3 .github/scripts/check_action_pins.py [path ...]   (default: .github)
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>[^\s#]+)\s*(?P<comment>#.*)?$")
SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
DOCKER = re.compile(r"^docker://[^@\s]+@sha256:[0-9a-f]{64}$")
VERSION_COMMENT = re.compile(r"^#\s*\S+")


def check_file(path: Path) -> list[str]:
    out: list[str] = []
    for no, line in enumerate(path.read_text().splitlines(), start=1):
        m = USES.match(line)
        if not m:
            continue
        ref = m["ref"].strip("'\"")
        if ref.startswith("./") or ref.startswith("../"):
            continue
        where = f"{path}:{no}: {ref}"
        if ref.startswith("docker://"):
            if not DOCKER.match(ref):
                out.append(f"{where}: docker reference must be pinned by @sha256 digest")
            continue
        if not SHA.match(ref):
            out.append(f"{where}: not pinned to a full 40-character commit SHA")
        elif not (m["comment"] and VERSION_COMMENT.match(m["comment"])):
            out.append(f"{where}: SHA pin needs a version comment (# vX.Y.Z)")
    return out


def workflow_files(root: Path) -> list[Path]:
    """A file itself, or every YAML under `<root>/workflows` and `<root>/actions`."""
    if root.is_file():
        return [root]
    return sorted(
        f for sub in ("workflows", "actions") for ext in ("yml", "yaml")
        for f in (root / sub).glob(f"**/*.{ext}")
    )  # fmt: skip


def check_paths(paths: list[Path]) -> list[str]:
    return [problem for p in paths for f in workflow_files(p) for problem in check_file(f)]


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or [Path(".github")]
    problems = check_paths(paths)
    for p in problems:
        print(f"::error::{p}" if "GITHUB_ACTIONS" in os.environ else p)
    if problems:
        print(f"{len(problems)} unpinned action reference(s). Pin to a commit SHA with a version "
              "comment, e.g. `uses: actions/checkout@<sha> # v4.2.2`.")
        return 1
    print("all action references are SHA-pinned")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
