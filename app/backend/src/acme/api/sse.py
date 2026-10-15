"""The SSE event stream of a journey (#305): it ends when it should and says so while idle.

A stream used to poll for as long as the process lived: a client that went away (or never read
again) kept the server polling the engine, and after a scenario restart it polled with the context
of another run (a `journey not found` storm). Now it stops

- when the client is gone (checked on every turn of the loop; the keep-alive comment makes a dead
  connection fail a write even through a proxy),
- when the run it was opened for is over (`still_open` turns false),
- after `SSE_MAX_SECONDS`, below the Cloud Run request timeout (300 s), with a clean end
  (EventSource clients reconnect on their own).
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


async def event_stream(
    request: Any,
    fetch: Callable[[int], Awaitable[list[dict[str, Any]]]],
    *,
    limit: int | None,
    poll_seconds: float,
    still_open: Callable[[], bool] = lambda: True,
) -> AsyncIterator[str]:
    """`data:` frames for the events `fetch(sent)` returns (those after the first `sent`)."""
    start = last_write = time.monotonic()
    sent = 0
    while limit is None or sent < limit:
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
        for event in batch[: None if limit is None else limit - sent]:
            sent += 1
            last_write = time.monotonic()
            yield f"data: {json.dumps(event)}\n\n"
        if not batch:
            if time.monotonic() - last_write >= SSE_KEEPALIVE_SECONDS:
                last_write = time.monotonic()
                yield KEEPALIVE
            await asyncio.sleep(poll_seconds)
