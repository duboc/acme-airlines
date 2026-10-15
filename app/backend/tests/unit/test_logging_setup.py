"""No fixture PII in ANY captured log record (third-party included), root at DEBUG."""

import logging

from tests.governance.flow import Flow

from acme.domain.fixtures import mariana_world
from acme.logging_setup import NOISY, configure_logging, redact


def pii() -> set[str]:
    world = mariana_world()
    values = set(world.pnrs)
    for p in world.passengers.values():
        values |= {p.name, *p.name.split(), p.loyalty.member_id}
    return values


async def test_scenario_at_debug_logs_no_pii(caplog):
    configure_logging()
    caplog.set_level(logging.DEBUG)  # root at DEBUG; noisy loggers stay pinned
    flow = Flow()
    try:
        await flow.acknowledged_replan()
    finally:
        await flow.http.aclose()
    text = " ".join(r.getMessage() for r in caplog.records) + caplog.text
    assert "MSLV42" in pii() and "Mariana" in pii()
    for value in pii():
        assert value not in text, f"PII leaked: {value[:3]}..."


def test_noisy_third_party_loggers_are_pinned():
    configure_logging()
    for name in NOISY:
        assert logging.getLogger(name).level >= logging.WARNING


def test_redaction_backstop_masks_fixture_pii_emails_and_phones(caplog):
    configure_logging()
    logger = logging.getLogger("some.other.lib")
    caplog.set_level(logging.DEBUG)
    logger.info("hello %s pnr %s ffp %s mail a.b@x.com tel +55 11 91234-5678", "Mariana Silva",
                "MSLV42", "FFP-0001")  # fmt: skip
    (rec,) = [r for r in caplog.records if r.name == "some.other.lib"]
    msg = rec.getMessage()
    assert "[redacted]" in msg
    for leaked in ("Mariana", "MSLV42", "FFP-0001", "a.b@x.com", "91234"):
        assert leaked not in msg
    assert redact("plain text") == "plain text"


def test_given_extra_fields_with_pii_when_logged_then_extra_attributes_are_redacted(caplog):
    configure_logging()
    logger = logging.getLogger("acme.extra.test")
    caplog.set_level(logging.DEBUG)
    logger.info(
        "structured event",
        extra={
            "pax": "Mariana Silva",
            "pnr": "MSLV42",
            "pax_id": "P-MARIANA",
            "nested": {
                "email": "a@b.com",
                "items": ["+55 11 91234-5678", ("FFP-0001", 42)],
            },
        },
    )
    (rec,) = [r for r in caplog.records if r.name == "acme.extra.test"]
    assert rec.pax == "[redacted]"
    assert rec.pnr == "[redacted]"
    assert rec.pax_id == "[redacted]"
    assert rec.nested == {"email": "[redacted]", "items": ["[redacted]", ("[redacted]", 42)]}


def test_engine_set_up_configures_logging(monkeypatch):
    from acme.engines.customer import build_customer_engine_app

    for name in NOISY:
        logging.getLogger(name).setLevel(logging.DEBUG)
    app = build_customer_engine_app()
    app.set_up()
    assert all(logging.getLogger(n).level >= logging.WARNING for n in NOISY)


def test_uvicorn_access_is_pinned_so_request_urls_never_reach_logs(caplog):
    configure_logging()
    assert "uvicorn.access" in NOISY
    caplog.set_level(logging.DEBUG)
    logging.getLogger("uvicorn.access").info('GET /passengers/P-MARIANA?phone=1 "200"')
    assert not [r for r in caplog.records if r.name == "uvicorn.access"]


ACCESS_LOG_SCRIPT = """
import threading, time
import httpx, uvicorn
from fastapi import FastAPI
from acme.logging_setup import configure_logging

configure_logging()  # as every entrypoint does, BEFORE uvicorn configures its own logging
app = FastAPI()

@app.get("/passengers/{name}")
def passenger(name: str):
    return {"ok": True}

server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="info"))
t = threading.Thread(target=server.run, daemon=True)
t.start()
while not server.started:
    time.sleep(0.05)
for path in ("/passengers/Mariana", "/passengers/x", "/missing"):
    httpx.get(f"http://127.0.0.1:PORT{path}")
server.should_exit = True
t.join(5)
"""


def test_uvicorn_access_log_has_no_logging_errors_and_stays_redacted():
    """#204: the redaction factory used to null `record.args`, which uvicorn's AccessFormatter
    unpacks, so every request printed a '--- Logging error ---' traceback."""
    import socket
    import subprocess
    import sys

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    run = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", ACCESS_LOG_SCRIPT.replace("PORT", str(port))],
        capture_output=True, text=True, timeout=60, check=True,
    )  # fmt: skip
    assert "Logging error" not in run.stderr, run.stderr
    out = run.stdout + run.stderr  # uvicorn writes access lines to stdout
    assert '"GET /passengers/' in out  # access lines are still emitted ...
    assert "Mariana" not in out  # ... and still redacted
    assert "[redacted]" in out
