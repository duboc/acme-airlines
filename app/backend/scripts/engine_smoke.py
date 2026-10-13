"""Clean-venv smoke test for the Agent Engine artifacts (#185). Run with the venv's python:

    python scripts/engine_smoke.py STAGED_DIR      # STAGED_DIR from `python -m acme.engines.stage`

Mimics container start-up: only the extracted `extra_packages` and the pip requirements are
available (the repo's `acme` is NOT installed); unpickle the engine, `set_up()` it and run a
query offline (LLM_MODE=fake). Fails loudly on a missing package, a package missing from the
tarball, a pickling problem or a `set_up()` exception.
"""

import asyncio
import os
import sys
from pathlib import Path

staged = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(staged / "extracted"))
os.environ.setdefault("LLM_MODE", "fake")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "493084803309")  # Agent Engine injects the NUMBER

import cloudpickle  # noqa: E402

import acme  # noqa: E402

assert Path(acme.__file__).resolve().is_relative_to(staged), f"acme came from {acme.__file__}"


def load(name: str):
    with open(staged / f"{name}.pkl", "rb") as fh:
        return cloudpickle.load(fh)


async def main() -> None:
    customer = load("customer")
    customer.set_up()
    diag = customer.diagnostics()
    assert diag["status"] == "ready", diag  # set_up() swallows errors into diagnostics (#185)
    events = [e async for e in customer.async_stream_query(message="poll", user_id="smoke")]
    assert events, "the Customer engine returned no events"
    ops = customer.register_operations()
    assert "a2a_skill" in ops["async"], ops
    sales = load("sales")  # set_up needs the live Customer engine, so only load + validate here
    assert hasattr(sales, "set_up") and hasattr(sales, "register_operations")
    print("engine smoke ok: customer set_up + query, sales loads; acme from", acme.__file__)


asyncio.run(main())
