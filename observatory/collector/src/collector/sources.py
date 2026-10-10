"""Row sources. A source takes the run ``ts`` and returns rows for one table.

The hub source lives in ``hub.py``; slice 3 (GitHub) replaces the remaining placeholder.
"""

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

from .hub import fixture_hub_source, live_hub_source

log = logging.getLogger(__name__)

Source = Callable[[str], list[dict]]

FIXTURES = Path(
    os.environ.get("OBSERVATORY_FIXTURES") or Path(__file__).resolve().parents[3] / "fixtures"
)


def fixture_source(table: str) -> Source:
    def read(ts: str) -> list[dict]:
        rows = json.loads((FIXTURES / f"{table}.json").read_text())
        return [{**r, "ts": ts} for r in rows]

    return read


def pending_source(table: str) -> Source:
    def skip(ts: str) -> list[dict]:
        log.warning("no live source for %s yet; skipping", table)
        return []

    return skip


def default_sources(mode: str) -> dict[str, Source]:
    make = fixture_source if mode == "fake" else pending_source
    sources = {"github_snapshots": make("github_snapshots")}
    if mode == "fake":
        sources["agent_snapshots"] = fixture_hub_source(FIXTURES / "hub_agents.json")
    else:
        sources["agent_snapshots"] = live_hub_source()
    return sources
