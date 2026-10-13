"""Readable, scrubbed engine start-up errors (#185).

A broken `set_up()` inside Agent Engine used to be an opaque "failed to deploy" with logs nobody
could read. The engines now catch it and expose `format_error(exc)` through the `diagnostics`
operation (and the deploy fails on it). That text crosses a trust boundary, so it is scrubbed:

- no local variable values (plain `traceback.format_exception`, never `capture_locals`);
- values of environment variables whose NAME looks secret (TOKEN, KEY, SECRET, PASSWORD, ...);
- credential shapes (Bearer tokens, `ya29.` and `AIza` Google tokens, PEM blocks, long opaque
  strings), passenger PII (fixture names, PNRs, emails, phones: `acme.logging_setup.redact`);
- truncated to `MAX_ERROR_CHARS`.
"""

from __future__ import annotations

import os
import re
import traceback

from acme.logging_setup import MASK, redact

MAX_ERROR_CHARS = 4000
_SECRET_NAME = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH", re.IGNORECASE)
_SHAPES = (
    re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.DOTALL),
    re.compile(r"Bearer\s+[\w.~+/=-]+", re.IGNORECASE),
    re.compile(r"ya29\.[\w.-]+"),
    re.compile(r"AIza[\w-]{20,}"),
    re.compile(r"[A-Za-z0-9_-]{40,}"),  # long opaque strings (no '/' or '.', so paths survive)
)


def scrub(text: str) -> str:
    for name, value in os.environ.items():
        if len(value) >= 6 and _SECRET_NAME.search(name):
            text = text.replace(value, MASK)
    for shape in _SHAPES:
        text = shape.sub(MASK, text)
    text = redact(text)
    if len(text) > MAX_ERROR_CHARS:
        text = text[:MAX_ERROR_CHARS] + " ...[truncated]"
    return text


def format_error(exc: BaseException) -> str:
    return scrub("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
