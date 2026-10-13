"""`acme-demo`: one container, one public port. Starts the simulators, the Customer runtime and the
Sales agent as loopback-only child processes and serves the front proxy on `$PORT` (default 8080).

Per-caller sim tokens are generated at startup, handed to each child through its own environment
only, and never logged. `LLM_MODE` defaults to `fake`.
"""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import uvicorn

from acme.demo.front import create_front
from acme.logging_setup import configure_logging

LOOPBACK = "127.0.0.1"
SIM_PORT, CUSTOMER_PORT, SALES_PORT = 8700, 8800, 8801
_BIN = Path(sys.executable).parent
_COMMANDS = {k: str(_BIN / f"acme-{k}") for k in ("sim", "customer", "sales")}


@dataclass(frozen=True)
class Tokens:
    sales: str = field(repr=False)
    sim_tokens: str = field(repr=False)


def make_tokens() -> Tokens:
    c, s, a = (secrets.token_hex(24) for _ in range(3))
    return Tokens(sales=s, sim_tokens=f"customer={c},sales={s},test-admin={a}")


def child_env(name: str, tokens: Tokens, base: Mapping[str, str]) -> dict[str, str]:
    env = {k: v for k, v in base.items() if k not in ("ACME_SIM_TOKENS", "ACME_SALES_TOKEN")}
    env.setdefault("LLM_MODE", "fake")
    env.setdefault("GOOGLE_CLOUD_PROJECT", "acme-airlines-factory")
    env.setdefault("ACME_GENAI_LOCATION", "global")
    if name == "sim":
        env.update(
            ACME_SIM_HOST=LOOPBACK, ACME_SIM_PORT=str(SIM_PORT), ACME_SIM_TOKENS=tokens.sim_tokens
        )
    elif name == "customer":
        env.update(ACME_CUSTOMER_HOST=LOOPBACK, ACME_CUSTOMER_PORT=str(CUSTOMER_PORT))
    elif name == "sales":
        env.update(
            ACME_SALES_HOST=LOOPBACK,
            ACME_SALES_PORT=str(SALES_PORT),
            ACME_SALES_TOKEN=tokens.sales,
            ACME_SIM_URL=f"http://{LOOPBACK}:{SIM_PORT}",
            ACME_CUSTOMER_URL=f"http://{LOOPBACK}:{CUSTOMER_PORT}",
        )
    return env


def _wait_ready(port: int, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(f"http://{LOOPBACK}:{port}/openapi.json", timeout=2)
            return
        except httpx.HTTPError:
            time.sleep(0.5)
    raise SystemExit("customer runtime did not become ready")


def engine_mode(env: Mapping[str, str]) -> str:
    return env.get("ENGINE_MODE") or "local"


def child_names(env: Mapping[str, str]) -> tuple[str, ...]:
    """`agent-engine`: only the API child (backed by the engines); no local sim/Sales/agents."""
    return ("customer",) if engine_mode(env) == "agent-engine" else ("sim", "customer")


def main() -> None:
    configure_logging()
    tokens = make_tokens()
    procs = [
        subprocess.Popen([_COMMANDS[n]], env=child_env(n, tokens, os.environ))  # noqa: S603
        for n in child_names(os.environ)
    ]
    _wait_ready(CUSTOMER_PORT)
    if engine_mode(os.environ) != "agent-engine":
        procs.append(
            subprocess.Popen([_COMMANDS["sales"]], env=child_env("sales", tokens, os.environ))  # noqa: S603
        )

    def stop(*_: object) -> None:
        for p in procs:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    upstream = httpx.AsyncClient(base_url=f"http://{LOOPBACK}:{CUSTOMER_PORT}", timeout=None)
    app = create_front(Path(os.environ.get("ACME_DEMO_STATIC", "/app/static")), upstream)
    try:
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))  # noqa: S104
    finally:
        for p in procs:
            p.terminate()
