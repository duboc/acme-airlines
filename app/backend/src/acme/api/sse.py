"""The SSE event stream of a journey (#305): it ends when it should and says so while idle.

A stream used to poll for as long as the process lived: a client that went away (or never read
again) kept the server polling the engine, and after a scenario restart it polled with the context
of another run (a `journey not found` storm). Now it stops

- when the client is gone (checked on every turn of the loop; the keep-alive comment makes a dead
  connection fail a write even through a proxy),
- when the run it was opened for is over (`still_open` turns false),
- after `SSE_MAX_SECONDS`, below the Cloud Run request timeout (300 s), with a clean end.

EventSource clients reconnect on their own after that (or a dropped connection), so every event
carries `id: <number of events up to and including it>` and a reconnect that sends
`Last-Event-ID` resumes after it instead of replaying the whole timeline (which the UI, appending
every event it receives, would show twice).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

SSE_KEEPALIVE_SECONDS = 15.0
SSE_MAX_SECONDS = 270.0
KEEPALIVE = ": keep-alive\n\n"  # an SSE comment: ignored by clients, a write that detects a proxy


def resume_from(header: str | None) -> int:
    """The `Last-Event-ID` of a reconnect: a non-negative integer, else 0 (from the start)."""
    try:
        value = int((header or "").strip())
    except ValueError:
        return 0
    return value if value >= 0 else 0


async def event_stream(
    request: Any,
    fetch: Callable[[int], Awaitable[list[dict[str, Any]]]],
    *,
    limit: int | None,
    poll_seconds: float,
    still_open: Callable[[], bool] = lambda: True,
    start_after: int = 0,
) -> AsyncIterator[str]:
    """`data:` frames for the events `fetch(sent)` returns (those after the first `sent`),
    starting after `start_after` events (a reconnect). `limit` counts the events of THIS stream."""
    start = last_write = time.monotonic()
    sent, emitted = start_after, 0
    while limit is None or emitted < limit:
        now = time.monotonic()
        if now - start >= SSE_MAX_SECONDS or not still_open() or await request.is_disconnected():
            return
        try:
            batch = await fetch(sent)
        except Exception:
            if not still_open():  # the run ended while the call was in flight: not an error
                return
            raise
        if not still_open():
            return
        for event in batch[: None if limit is None else limit - emitted]:
            sent += 1
            emitted += 1
            last_write = time.monotonic()
            yield f"id: {sent}\ndata: {json.dumps(event)}\n\n"
        if not batch:
            if time.monotonic() - last_write >= SSE_KEEPALIVE_SECONDS:
                last_write = time.monotonic()
                yield KEEPALIVE
            await asyncio.sleep(poll_seconds)
