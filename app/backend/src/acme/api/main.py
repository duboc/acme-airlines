"""ASGI entrypoint: `uvicorn acme.api.main:app`."""

from acme.api.app import create_app

app = create_app()
