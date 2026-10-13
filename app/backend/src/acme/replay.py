"""Replay mode (#266): rebuilding scenario state from the seeded mocks must be invisible outside.

The Customer engine rebuilds a scenario by replaying its steps when a call lands on a container
that never saw it. The state being recreated was already reported by the container that lost it,
so a replay emits no spans and no metrics (see `acme.telemetry.span` and `acme.metrics`).
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator

_REPLAYING: contextvars.ContextVar[bool] = contextvars.ContextVar("acme_replaying", default=False)


def is_replaying() -> bool:
    return _REPLAYING.get()


@contextlib.contextmanager
def replaying() -> Iterator[None]:
    token = _REPLAYING.set(True)
    try:
        yield
    finally:
        _REPLAYING.reset(token)
