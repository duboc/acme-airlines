"""ASGI entrypoint: `uvicorn acme.api.main:app`."""

from acme.api.app import create_app
from acme.logging_setup import configure_logging

configure_logging()
app = create_app()
