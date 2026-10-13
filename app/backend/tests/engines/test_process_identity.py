"""#266 step 1: every engine operation carries a non-PII process identity, so a 'no scenario is
started' can be matched to the process/container that handled scenario_start (or not)."""

import json
import logging
import os
import re
import subprocess
import sys
import textwrap

import pytest

from acme import telemetry
from acme.engines.customer import EngineOperationError, build_customer_engine_app


class Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        msg = record.getMessage()
        if msg.startswith("ACME_DIAG "):
            self.records.append(json.loads(msg[len("ACME_DIAG ") :]))


@pytest.fixture
def lines():
    h = Lines()
    logger = logging.getLogger("acme.diag")
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    yield h.records
    logger.removeHandler(h)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("ACME_STATE_DB", raising=False)


def worker():
    app = build_customer_engine_app()
    app.set_up()
    return app


def ops(lines, op):
    return [r for r in lines if r["event"] == "op" and r["op"] == op]


def test_process_identity_has_ids_only():
    ident = telemetry.process_identity()
    assert set(ident) == {"proc", "pid", "container"}
    assert re.fullmatch(r"[0-9a-f]{8}", ident["proc"])
    assert ident["pid"] == os.getpid()
    assert re.fullmatch(r"[0-9a-f]{8}|na", ident["container"])
    assert telemetry.process_identity() == ident  # stable within a process


def test_every_op_line_carries_identity_scenario_and_shared_state_versions(lines):
    app = worker()
    app.scenario_start("happy-path")
    app.journeys_list()
    (start,) = ops(lines, "scenario_start")
    (listing,) = ops(lines, "journeys_list")
    ident = telemetry.process_identity()
    for line in (start, listing):
        assert line["proc"] == ident["proc"] and line["container"] == ident["container"]
        assert line["scenario"] == "happy-path" and line["step"] == 0
        assert {"shared_version", "loaded_version", "db_inode"} <= set(line)


def test_without_a_scenario_the_line_says_so(lines):
    app = worker()
    app.journeys_list()
    (line,) = ops(lines, "journeys_list")
    assert line["scenario"] is None and line["step"] is None


def test_the_no_scenario_error_names_the_process_that_answered():
    app = worker()
    with pytest.raises(EngineOperationError) as exc:
        app.scenario_state()
    text = str(exc.value)
    ident = telemetry.process_identity()
    assert text.startswith("no scenario is started")
    assert f"proc={ident['proc']}" in text and f"pid={ident['pid']}" in text
    assert f"container={ident['container']}" in text and "scenario=none" in text
    assert re.fullmatch(r"[A-Za-z0-9_ =:,()./\-]+", text)  # ids and counters only


async def test_the_error_of_a_step_in_another_real_process_shows_a_different_identity(
    lines, tmp_path
):
    """The failure mode of the live proof, for real: process A starts the scenario in ITS memory;
    process B (this one) is asked to step and answers 'no scenario', naming itself."""
    code = textwrap.dedent(
        """
        import json
        from acme.engines.customer import build_customer_engine_app
        from acme import telemetry
        app = build_customer_engine_app(); app.set_up()
        app.scenario_start("happy-path")
        print(json.dumps(telemetry.process_identity()))
        """
    )
    env = {k: v for k, v in os.environ.items() if k != "ACME_STATE_DB"}
    out = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True, env=env,
        check=True, timeout=180,
    )  # fmt: skip
    other = json.loads(out.stdout.strip().splitlines()[-1])
    app = worker()
    with pytest.raises(EngineOperationError) as exc:
        await app.scenario_step()
    mine = telemetry.process_identity()
    assert other["proc"] != mine["proc"] and other["pid"] != mine["pid"]
    assert f"proc={mine['proc']}" in str(exc.value) and other["proc"] not in str(exc.value)
    (line,) = ops(lines, "scenario_step")
    assert line["proc"] == mine["proc"] and line["scenario"] is None


def test_diagnostics_reports_the_identity():
    app = worker()
    d = app.diagnostics()
    assert d["identity"] == telemetry.process_identity()
    assert set(d) == {"engine", "status", "startup_error", "llm_mode", "identity"}
