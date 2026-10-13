from __future__ import annotations

import json
import sqlite3
from typing import Protocol, runtime_checkable

from acme.domain.clock import Clock
from acme.domain.journey import (
    DisruptionStatus,
    JourneyEvent,
    JourneyState,
    OfferRecord,
    PassengerRequest,
)


@runtime_checkable
class JourneyStore(Protocol):
    def save(self, journey: JourneyState) -> None: ...
    def load(self, journey_id: str, clock: Clock | None = None) -> JourneyState | None: ...
    def ids(self) -> list[str]: ...
    def delete_except(self, keep: set[str]) -> None: ...
    def close(self) -> None: ...


def dump_journey(j: JourneyState) -> str:
    return json.dumps(
        {
            "status": j.status.value,
            "resolution_acknowledged": j.resolution_acknowledged,
            "passenger_tier": j.passenger_tier,
            "current_flight": j.current_flight,
            "open_requests": [r.model_dump(mode="json") for r in j.open_requests],
            "offers": [r.model_dump(mode="json") for r in j.offers],
            "timeline": [e.model_dump(mode="json") for e in j.timeline],
        }
    )


class SqliteJourneyStore:
    """One row per journey (latest snapshot). `path=":memory:"` for tests."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        if path != ":memory:":  # several processes share one file (#200)
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS journeys (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)"
        )
        self._db.commit()

    def save(self, journey: JourneyState) -> None:
        self._db.execute(
            "INSERT INTO journeys (id, snapshot) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET snapshot = excluded.snapshot",
            (journey.journey_id, dump_journey(journey)),
        )
        self._db.commit()

    def load(self, journey_id: str, clock: Clock | None = None) -> JourneyState | None:
        row = self._db.execute(
            "SELECT snapshot FROM journeys WHERE id = ?", (journey_id,)
        ).fetchone()
        if row is None:
            return None
        d = json.loads(row[0])
        return JourneyState.restore(
            journey_id,
            status=DisruptionStatus(d["status"]),
            resolution_acknowledged=d["resolution_acknowledged"],
            open_requests=[PassengerRequest.model_validate(r) for r in d["open_requests"]],
            offers=[OfferRecord.model_validate(o) for o in d["offers"]],
            timeline=[JourneyEvent.model_validate(e) for e in d["timeline"]],
            clock=clock,
            passenger_tier=d["passenger_tier"],
            current_flight=d["current_flight"],
        )

    def ids(self) -> list[str]:
        return [r[0] for r in self._db.execute("SELECT id FROM journeys ORDER BY id")]

    def delete_except(self, keep: set[str]) -> None:
        """Drop every journey not in `keep` (a scenario reset removes them from the shared DB)."""
        stale = [i for i in self.ids() if i not in keep]
        self._db.executemany("DELETE FROM journeys WHERE id = ?", [(i,) for i in stale])
        self._db.commit()

    def close(self) -> None:
        self._db.close()
