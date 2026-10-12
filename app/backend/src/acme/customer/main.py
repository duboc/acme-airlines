"""`uv run acme-customer`: serve the runtime API (Customer agent + in-process simulators)."""

import os

import uvicorn

from acme.api.app import create_app
from acme.logging_setup import configure_logging


def main() -> None:
    configure_logging()
    port = int(os.environ.get("ACME_CUSTOMER_PORT", "8800"))
    uvicorn.run(create_app(), host=os.environ.get("ACME_CUSTOMER_HOST", "127.0.0.1"), port=port)
