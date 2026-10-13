"""Shared demo state across the processes of one Agent Engine container (#200).

Agent Engine runs several agent processes per container and `max_instances=1` is one container,
so processes share the container's disk but not memory. `SharedState` makes every engine
operation run as: take the cross-process lock, load the newest snapshot if another process
wrote one, run, write a new snapshot if anything changed, release.

- The lock is `fcntl.flock` on `<db>.lock`, a fresh file descriptor per acquisition, so it also
  excludes other threads and tasks of the same process. Because the WHOLE operation holds it, the
  per-journey guarantees of #88 and #97 (one charge, one notification) hold across processes:
  two operations never interleave. It is reentrant within a task/thread (nested operations do not
  deadlock) and acquired by polling, so a cancelled waiter never leaks it. A wait longer than
  `lock_timeout` is a `LockTimeout` error, not a hang.
- State: the runtime snapshot (`acme.state.snapshot`, pydantic JSON) in table `meta`, journey
  states in the existing `journeys` table of `SqliteJourneyStore` (#16), same SQLite file (WAL).
- Container-local and ephemeral, exactly like the in-memory state it replaces: a restart starts
  clean, and `max_instances=1` stays mandatory (a second container would not share the disk).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import fcntl
import logging
import os
import sqlite3
import time
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

from acme.memory.store import SqliteJourneyStore
from acme.state.snapshot import capture, restore

if TYPE_CHECKING:
    from acme.customer.runtime import CustomerRuntime

log = logging.getLogger(__name__)
POLL_SECONDS = 0.005
_HELD: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "acme_state_held", default=frozenset()
)


class LockTimeout(RuntimeError):
    """The cross-process lock was not available within `lock_timeout`."""


class SharedState:
    def __init__(self, path: str, *, lock_timeout: float = 60.0) -> None:
        self.path = path
        self._lock_path = f"{path}.lock"
        self._key = f"{id(self)}"  # reentrancy is per SharedState object, not per file
        self._lock_timeout = lock_timeout
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._loaded_version: int | None = None
        self._last_json: str | None = None
        # Opening the file switches it to WAL and creates tables, which fails with "database is
        # locked" when two processes start at once: do it under the cross-process lock.
        with self.exclusive_sync():
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self._db.commit()
            self._journeys = SqliteJourneyStore(path)  # the #16 adapter, same file

    # --- lock -------------------------------------------------------------------------------
    def _try_lock(self) -> int | None:
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        return fd

    @staticmethod
    def _release(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def _timeout(self) -> LockTimeout:
        return LockTimeout(f"shared-state lock not acquired within {self._lock_timeout}s")

    async def _acquire_async(self) -> int:
        deadline = time.monotonic() + self._lock_timeout
        while (fd := self._try_lock()) is None:
            if time.monotonic() > deadline:
                raise self._timeout()
            await asyncio.sleep(POLL_SECONDS)  # polling: cancellation cannot leak the lock
        return fd

    def _acquire_sync(self) -> int:
        deadline = time.monotonic() + self._lock_timeout
        while (fd := self._try_lock()) is None:
            if time.monotonic() > deadline:
                raise self._timeout()
            time.sleep(POLL_SECONDS)
        return fd

    @contextlib.contextmanager
    def exclusive_sync(self) -> Iterator[None]:
        """The bare cross-process lock (blocking), reentrant per thread."""
        if self._key in _HELD.get():
            yield
            return
        fd = self._acquire_sync()
        token = _HELD.set(_HELD.get() | {self._key})
        try:
            yield
        finally:
            _HELD.reset(token)
            self._release(fd)

    # --- snapshot io (call only while holding the lock) -------------------------------------
    def _get(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def version(self) -> int:
        v = self._get("version")
        return int(v) if v else 0

    def _load(self, rt: CustomerRuntime, scenarios: Any | None) -> None:
        version = self.version()
        if version == self._loaded_version:
            return
        text = self._get("snapshot")
        if text:
            try:
                restore(rt, text, self._journeys, scenarios)
            except ValueError:
                log.warning("ignoring an unreadable shared snapshot (v%s)", version, exc_info=True)
        self._loaded_version, self._last_json = version, text

    def _save(self, rt: CustomerRuntime, scenarios: Any | None) -> None:
        text = capture(rt, self._journeys, scenarios, save=False)
        if text == self._last_json:
            return  # nothing changed: no write, no version bump
        capture(rt, self._journeys, scenarios, save=True)  # journeys -> `journeys` table
        version = self.version() + 1
        with self._db:
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES ('snapshot', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (text,),
            )
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES ('version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(version),),
            )
        self._loaded_version, self._last_json = version, text

    # --- one engine operation ---------------------------------------------------------------
    @contextlib.asynccontextmanager
    async def operation(
        self, rt: CustomerRuntime, scenarios: Any | None = None
    ) -> AsyncIterator[None]:
        if self._key in _HELD.get():
            yield
            return
        fd = await self._acquire_async()
        token = _HELD.set(_HELD.get() | {self._key})
        try:
            self._load(rt, scenarios)
            try:
                yield
            finally:
                self._save(rt, scenarios)
        finally:
            _HELD.reset(token)
            self._release(fd)

    @contextlib.contextmanager
    def operation_sync(self, rt: CustomerRuntime, scenarios: Any | None = None) -> Iterator[None]:
        with self.exclusive_sync():
            self._load(rt, scenarios)
            try:
                yield
            finally:
                self._save(rt, scenarios)
