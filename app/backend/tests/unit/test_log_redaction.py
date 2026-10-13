"""#233: redaction must hold on the error paths (tracebacks, stack info) and for passengers
that are NOT in the fixture (pattern-based: email, E.164 phone, PNR shape, passenger id)."""

import asyncio
import io
import logging

import httpx
import pytest

from acme.api.app import create_app
from acme.logging_setup import MASK, configure_logging, redact

# A passenger that is not in the Mariana fixture.
EMAIL, PHONE, PNR, PAX_ID, FFP = (
    "joana.silva@example.com",
    "+55 21 98888-7777",
    "XQZ9T2",
    "P-JOANA",
    "FFP-0042",
)
LEAKS = (EMAIL, "98888", PNR, PAX_ID, FFP, "joana.silva")


@pytest.fixture
def capture():
    """A real handler + formatter, exactly what ends up in the logs."""
    configure_logging()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("acme.test.redaction")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, stream
    logger.removeHandler(handler)


def assert_clean(text):
    for leak in LEAKS:
        assert leak not in text, f"leaked {leak[:4]}..."


def test_non_fixture_passenger_is_masked_by_patterns():
    text = f"rebooking {PAX_ID} pnr {PNR} mail {EMAIL} tel {PHONE} member {FFP}"
    out = redact(text)
    assert_clean(out)
    assert out.count(MASK) >= 5
    assert redact("flight AC1234 on J-0a1b2c3d trace " + "a" * 32) == (
        "flight AC1234 on J-0a1b2c3d trace " + "a" * 32  # not PII: flight no., journey id, trace id
    )


@pytest.mark.parametrize("pnr", ["XQZ9T2", "ABC123", "MSLV42", "7K2P9Q"])
def test_pnr_shapes_are_masked(pnr):
    assert pnr not in redact(f"booking {pnr} confirmed")


@pytest.mark.parametrize("plain", ["AC1234", "AC0800", "HT8842", "OFFSET", "GRU", "J-0a1b2c3d"])
def test_ordinary_ids_are_not_masked(plain):
    assert redact(f"x {plain} y") == f"x {plain} y"


def test_traceback_of_an_exception_with_passenger_data_is_redacted(capture):
    logger, stream = capture
    try:
        raise ValueError(f"cannot rebook {PAX_ID} ({EMAIL}, {PHONE}) on {PNR}, member {FFP}")
    except ValueError:
        logger.exception("rebooking failed")
    out = stream.getvalue()
    assert "Traceback (most recent call last)" in out and "ValueError" in out  # still useful
    assert_clean(out)


def test_chained_exceptions_are_redacted_too(capture):
    logger, stream = capture
    try:
        try:
            raise KeyError(PNR)
        except KeyError as inner:
            raise RuntimeError(f"lookup for {EMAIL} failed") from inner
    except RuntimeError:
        logger.error("boom", exc_info=True)
    out = stream.getvalue()
    assert "KeyError" in out and "RuntimeError" in out
    assert_clean(out)


def test_stack_info_and_args_are_redacted(capture):
    logger, stream = capture
    logger.warning("passenger %s mail %s", PAX_ID, EMAIL, stack_info=True)
    assert_clean(stream.getvalue())


def test_exc_info_from_a_stored_tuple_is_redacted(capture):
    logger, stream = capture
    import sys

    try:
        raise OSError(f"{PHONE} unreachable")
    except OSError:
        info = sys.exc_info()
    logger.error("late", exc_info=info)
    assert_clean(stream.getvalue())


async def test_operator_timeline_shows_passenger_text_but_it_is_never_logged(caplog):
    """The SSE timeline is for the console (behind IAP) and carries the passenger's words; no log
    record, at any level, may contain them."""
    configure_logging()
    caplog.set_level(logging.DEBUG)
    secret = "my-unique-free-text-zq81 please call me"
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/scenario/trigger-cancellation", json={"flight": "AC1234", "reason": "maintenance"}
        )
        (jid,) = r.json()["journey_ids"]
        await c.post("/whatsapp/P-MARIANA/messages", json={"text": secret})
        n = len(app.state.runtime.journeys[jid].state.timeline)  # the stream closes after N
        sse = (await asyncio.wait_for(c.get(f"/journeys/{jid}/events?limit={n}"), timeout=20)).text
    assert secret in sse  # shown to the console
    logged = " ".join(r.getMessage() + (r.exc_text or "") for r in caplog.records) + caplog.text
    assert "zq81" not in logged
