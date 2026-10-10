"""`uv run acme-customer`: serve the runtime API (Customer agent + in-process simulators)."""

import os

import uvicorn

from acme.api.app import create_app


def main() -> None:
    port = int(os.environ.get("ACME_CUSTOMER_PORT", "8800"))
    uvicorn.run(create_app(), host="127.0.0.1", port=port)
