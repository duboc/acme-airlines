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
