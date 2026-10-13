"""The Customer runtime's own background loop (#228).

Scenario 1 (silent ingestion) and scenario 3 (the proactive WhatsApp at 22:09) must happen with no
API client connected, and the #229 service closure must advance without anyone polling. Each
iteration ingests new OCC events (`CustomerRuntime.poll`, the mock OCC subscription) and then runs
`CustomerRuntime.tick()` (due notifications and no-offer closures, driven by the runtime's Clock).
`tick` and the ingestion take the same per-journey / runtime locks as every other path, so the loop
cannot interleave with a passenger's reply or an accept.

A failure in an iteration is logged (the exception type only: messages can carry tool payloads) and
retried with exponential backoff (full jitter); it never ends the loop. Cancellation (shutdown) is
the only way out, and it is clean.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acme.customer.runtime import CustomerRuntime

log = logging.getLogger(__name__)


class CustomerLoop:
    def __init__(
        self,
        rt: CustomerRuntime,
        *,
        interval: float = 1.0,
        backoff_base: float = 1.0,
        backoff_cap: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._rt = rt
        self._interval, self._base, self._cap = interval, backoff_base, backoff_cap
        self._sleep, self._jitter = sleep, jitter
        self._task: asyncio.Task[None] | None = None

    async def step(self) -> None:
        """One iteration: ingest OCC events, then run the time-driven work."""
        await self._rt.poll(timeout_s=0)
        await self._rt.tick()

    def _delay(self, failures: int) -> float:
        ceiling = min(self._cap, self._base * 2 ** (failures - 1))
        return ceiling * (0.5 + self._jitter() / 2)  # jitter 1.0 -> the full ceiling

    async def run(self) -> None:
        failures = 0
        while True:
            try:
                await self.step()
                failures = 0
                delay = self._interval
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                failures += 1
                delay = self._delay(failures)
                log.warning(
                    "customer loop iteration failed (%s), retry #%d in %.1fs",
                    type(exc).__name__, failures, delay,
                )  # fmt: skip
            await self._sleep(delay)

    # --- lifecycle -------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.get_running_loop().create_task(self.run(), name="customer-loop")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
