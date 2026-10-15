"""Opt-in live proof (#167): presets 1-5 on the DEPLOYED engines, with Cloud Trace asserted.

Skipped unless `ACME_LIVE_AGENT_ENGINE=1` and both `AGENT_ENGINE_CUSTOMER` / `AGENT_ENGINE_SALES`
are set; deselected in CI (`-m 'not live'`). Set `ACME_PROOF_TABLE` to write the Markdown table to
a file (stdout otherwise). Uses ADC. Design: docs/agent-engine/engines.md#proof
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.live.agent_engine_proof import (
    CloudLogging,
    CloudTraceV1,
    probe_report,
    run_proof,
    run_rebuild_proof,
    write_engine_logs,
    write_inventory,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (
            os.environ.get("ACME_LIVE_AGENT_ENGINE") == "1"
            and os.environ.get("AGENT_ENGINE_CUSTOMER")
            and os.environ.get("AGENT_ENGINE_SALES")
        ),
        reason="needs ACME_LIVE_AGENT_ENGINE=1, AGENT_ENGINE_CUSTOMER and AGENT_ENGINE_SALES",
    ),
]


async def test_deployed_engines_emit_the_expected_traces_without_pii(capsys):
    from acme.api.engine_backend import EngineBackend
    from tests.api.test_engine_mode import engine_backend as local_engine_backend

    backend = EngineBackend.from_env()
    project = os.environ["AGENT_ENGINE_CUSTOMER"].split("/")[1]
    presets = (
        tuple(os.environ.get("ACME_PROOF_PRESETS", "").split(","))
        if os.environ.get("ACME_PROOF_PRESETS")
        else None
    )

    kwargs = {"presets": presets} if presets else {}
    started = datetime.now(UTC)
    report = await run_proof(
        backend,
        local_engine_backend(),
        CloudTraceV1(project),
        project=project,
        dump=os.environ.get("ACME_PROOF_DUMP") == "1",
        **kwargs,
    )

    table = report.markdown()
    target = os.environ.get("ACME_PROOF_TABLE")
    if target:
        Path(target).write_text(table)
    else:
        print(table)
    inventory_path = os.environ.get("ACME_PROOF_INVENTORY")
    if inventory_path and report.inventory is not None:
        write_inventory(report, inventory_path)  # on failure or ACME_PROOF_DUMP=1
    attach_engine_logs(project, started)  # even when the proof failed: that is when it matters
    assert not report.failures, "\n".join(report.failures)


def attach_engine_logs(project: str, started: datetime) -> None:
    """Engine diagnostics for the run window (#221): allowlisted ACME_DIAG fields only, see
    `write_engine_logs`. A log read failure is written to the file as a marker, never fatal."""
    path = os.environ.get("ACME_PROOF_LOGS")
    if not path:
        return
    logs = CloudLogging(project)
    start, end = started - timedelta(minutes=1), datetime.now(UTC) + timedelta(minutes=2)
    write_engine_logs(
        path,
        {
            "customer": logs.fetch_or_error(os.environ["AGENT_ENGINE_CUSTOMER"], start, end),
            "sales": logs.fetch_or_error(os.environ["AGENT_ENGINE_SALES"], start, end),
        },
    )


async def test_forced_rebuild_matches_the_plain_run_without_repeating_side_effects():
    """#279: park the deployed Customer engine on another preset, ask for the final step of the
    reference preset (a cross-container rebuild), and compare with the plain run."""
    from acme.api.engine_backend import EngineBackend

    backend = EngineBackend.from_env()
    project = os.environ["AGENT_ENGINE_CUSTOMER"].split("/")[1]
    logs = CloudLogging(project)

    def fetch(since: datetime) -> list[dict]:
        return logs.fetch_or_error(
            os.environ["AGENT_ENGINE_CUSTOMER"], since, datetime.now(UTC) + timedelta(minutes=2)
        )

    started = datetime.now(UTC)
    report = await run_rebuild_proof(backend, fetch)
    summary = report.markdown()
    print(summary)
    if target := os.environ.get("ACME_PROOF_REBUILD"):
        Path(target).write_text(summary)
    attach_engine_logs(project, started)
    assert not report.failures, "\n".join(report.failures)


@pytest.mark.skipif(
    int(os.environ.get("ACME_PROBE_CALLS", "0") or 0) <= 0, reason="set ACME_PROBE_CALLS=N"
)
async def test_unary_probe_counts_customer_traces_for_read_only_calls():
    """Platform sampling vs our exporter (#221): N back-to-back and N//2 spaced read-only
    `journeys_list` calls, then count the `acme.engine.op` traces. Diagnostic: it reports the
    loss (and writes ACME_PROBE_TABLE / the engine logs) but does not fail."""
    from acme.api.engine_backend import EngineBackend

    n = int(os.environ["ACME_PROBE_CALLS"])
    customer = EngineBackend.from_env()._customer  # noqa: SLF001
    project = os.environ["AGENT_ENGINE_CUSTOMER"].split("/")[1]
    started, calls = datetime.now(UTC), []

    async def one(kind: str) -> None:
        t0 = datetime.now(UTC)
        await customer.call("journeys_list")
        calls.append({"kind": kind, "t0": t0, "t1": datetime.now(UTC)})

    for _ in range(n):
        await one("back_to_back")
    await asyncio.sleep(10)
    for _ in range(max(n // 2, 3)):
        await one("spaced")
        await asyncio.sleep(20)
    await asyncio.sleep(90)  # let Cloud Trace ingest
    traces = await CloudTraceV1(project).list_traces(started, datetime.now(UTC))
    report = probe_report(calls, traces)
    summary = (
        f"## Unary probe\n\nsent {report['sent']}, traces found {report['found']}, "
        f"lost calls {report['lost']}\n\nby kind: {report['by_kind']}\n"
    )
    print(summary)
    if target := os.environ.get("ACME_PROBE_TABLE"):
        Path(target).write_text(summary)
    attach_engine_logs(project, started)
