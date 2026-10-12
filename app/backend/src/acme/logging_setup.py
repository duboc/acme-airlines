"""Logging hygiene for every runtime entrypoint: no passenger PII in logs.

`configure_logging()`:
1. pins chatty third-party loggers (mcp wire dumps, httpx, google-genai, ADK ...) to WARNING+;
2. installs a redaction backstop on the log record factory, masking fixture PII (passenger
   names, PNR locators, loyalty ids), emails and phone numbers before ANY handler sees the
   record, so it also covers handlers added later.
It is idempotent and does not touch the root level or add handlers.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

NOISY = (
    "mcp", "httpx", "httpcore", "google_genai", "google.genai", "google.adk", "google_adk",
    "google.auth", "urllib3", "asyncio", "a2a", "sse_starlette",
)  # fmt: skip
MASK = "[redacted]"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+\d[\d\s().-]{7,}\d")


@lru_cache(maxsize=1)
def _literals() -> re.Pattern[str] | None:
    from acme.domain.fixtures import mariana_world  # noqa: PLC0415

    world = mariana_world()
    values: set[str] = set(world.pnrs)
    for p in world.passengers.values():
        values |= {p.name, *p.name.split()}
        if p.loyalty:
            values.add(p.loyalty.member_id)
        if p.phone:
            values.add(p.phone)
    values = {v for v in values if len(v) >= 4}
    if not values:
        return None
    return re.compile("|".join(re.escape(v) for v in sorted(values, key=len, reverse=True)))


def redact(text: str) -> str:
    literals = _literals()
    if literals:
        text = literals.sub(MASK, text)
    return _PHONE.sub(MASK, _EMAIL.sub(MASK, text))


_installed = False


def configure_logging() -> None:
    global _installed
    for name in NOISY:
        logger = logging.getLogger(name)
        if logger.level == logging.NOTSET or logger.level < logging.WARNING:
            logger.setLevel(logging.WARNING)
    if _installed:
        return
    _installed = True
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        try:
            record.msg = redact(record.getMessage())
            record.args = None
        except Exception:  # noqa: BLE001 (logging must never raise)
            pass
        return record

    logging.setLogRecordFactory(factory)
