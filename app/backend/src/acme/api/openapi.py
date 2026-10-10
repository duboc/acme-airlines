"""Render / write the committed OpenAPI contract.

Regenerate with: `uv run python -m acme.api.openapi`
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

from acme.api.app import create_app

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "openapi.json"


def render_openapi(app: FastAPI) -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    CONTRACT_PATH.write_text(render_openapi(create_app()))
    print(f"wrote {CONTRACT_PATH}")
