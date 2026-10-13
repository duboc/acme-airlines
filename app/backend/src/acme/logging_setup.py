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
import traceback
from functools import lru_cache

NOISY = (
    "mcp", "httpx", "httpcore", "google_genai", "google.genai", "google.adk", "google_adk",
    "google.auth", "urllib3", "asyncio", "a2a", "sse_starlette", "uvicorn.access",
)  # fmt: skip
MASK = "[redacted]"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+\d[\d\s().-]{7,}\d")
# Passengers that are NOT in the fixture (#233): shapes, not literals.
_PAX_ID = re.compile(r"\bP-[A-Za-z0-9]{2,}\b")  # passenger ids: P-MARIANA, P-JOANA
_MEMBER = re.compile(r"\bFFP-\d{3,}\b")  # loyalty member ids
# PNR: 6 upper-case letters/digits with at least one of each, but not a flight number (two
# letters + 3-4 digits: AC1234), which is not personal data.
_PNR = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])(?![A-Z]{2}\d{3,4}(?![A-Za-z0-9]))"
    r"[A-Z0-9]{6}(?![A-Za-z0-9])"
)


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
    for pattern in (_EMAIL, _PHONE, _PAX_ID, _MEMBER, _PNR):
        text = pattern.sub(MASK, text)
    return text


def _redact_error_paths(record: logging.LogRecord) -> None:
    """The traceback and stack are formatted by the handler, after the message was redacted, so
    they must be redacted here (#233): exception messages carry tool payloads on exactly the error
    paths. The formatted traceback (the whole `__cause__`/`__context__` chain) replaces
    `exc_info`; handlers print `exc_text`."""
    if record.exc_info and record.exc_info[0] is not None:
        formatted = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
        record.exc_text = redact(formatted)
        record.exc_info = None
    elif record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)


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
            if record.name == "uvicorn.access" and isinstance(record.args, tuple):
                # uvicorn's AccessFormatter unpacks the 5 args (#204): keep the tuple, mask each.
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
                return record
            record.msg = redact(record.getMessage())
            record.args = None
            _redact_error_paths(record)
        except Exception:  # noqa: BLE001 (logging must never raise)
            pass
        return record

    logging.setLogRecordFactory(factory)
