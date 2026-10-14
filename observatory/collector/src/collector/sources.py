"""Row sources. A source takes the run ``ts`` and returns rows for one table.

Live sources: ``hub.py`` (agents) and ``github.py`` (GitHub flow).
"""

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

from .github import fixture_github_source, live_github_source
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


def default_sources(mode: str) -> dict[str, Source]:
    if mode == "fake":
        return {
            "agent_snapshots": fixture_hub_source(FIXTURES / "hub_agents.json"),
            "github_snapshots": fixture_github_source(),
        }
    return {"agent_snapshots": live_hub_source(), "github_snapshots": live_github_source()}
