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


BUSY_TIMEOUT_MS = 10_000  # how long a writer waits for another process's write lock (#288)


def configure_connection(db: sqlite3.Connection) -> None:
    """WAL (readers never block the writer and vice versa), an explicit `busy_timeout` (a
    contended write waits instead of failing with "database is locked") and `synchronous=NORMAL`
    (safe with WAL: a crash may lose the last commit, never corrupt the file; the state is
    container-local and ephemeral anyway). Applied to every connection on a shared file (#288)."""
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    db.execute("PRAGMA synchronous=NORMAL")


class SqliteJourneyStore:
    """One row per journey (latest snapshot). `path=":memory:"` for tests.

    With `connection`, the store writes through the caller's connection and never commits: the
    caller owns the transaction (`SharedState` saves the journeys and the snapshot atomically)."""

    def __init__(
        self, path: str = ":memory:", connection: sqlite3.Connection | None = None
    ) -> None:
        self._own = connection is None
        self._db = connection or sqlite3.connect(path, check_same_thread=False, timeout=10)
        if self._own and path != ":memory:":  # several processes share one file (#200)
            configure_connection(self._db)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS journeys (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)"
        )
        self._commit()

    def _commit(self) -> None:
        if self._own:
            self._db.commit()

    def save(self, journey: JourneyState) -> None:
        self._db.execute(
            "INSERT INTO journeys (id, snapshot) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET snapshot = excluded.snapshot",
            (journey.journey_id, dump_journey(journey)),
        )
        self._commit()

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
        self._commit()

    def close(self) -> None:
        self._db.close()
