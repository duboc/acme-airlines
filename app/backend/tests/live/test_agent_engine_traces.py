"""Opt-in live proof (#167): presets 1-5 on the DEPLOYED engines, with Cloud Trace asserted.

Skipped unless `ACME_LIVE_AGENT_ENGINE=1` and both `AGENT_ENGINE_CUSTOMER` / `AGENT_ENGINE_SALES`
are set; deselected in CI (`-m 'not live'`). Set `ACME_PROOF_TABLE` to write the Markdown table to
a file (stdout otherwise). Uses ADC. Design: docs/agent-engine/engines.md#proof
"""

import os
from pathlib import Path

import pytest

from tests.live.agent_engine_proof import CloudTraceV1, run_proof, write_inventory

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
    assert not report.failures, "\n".join(report.failures)
