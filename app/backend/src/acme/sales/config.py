"""The Sales agent's MCP configuration: the inventory server only, with the sales token."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from acme.sim.auth import client_config

SALES_SERVERS = ("inventory",)


def sales_mcp_config(base_url: str, tokens: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    return client_config("sales", base_url, tokens, servers=SALES_SERVERS)
