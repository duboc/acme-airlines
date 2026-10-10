"""ADK `MCPToolset` implementation of the `Connector` seam (ADR 0100).

Each server is reached through its own `MCPToolset`; tool calls go through ADK's `MCPTool`
so the Customer runtime uses ADK's MCP stack. It needs a real HTTP endpoint (ADK builds its own
transport), so it targets a running `acme-sim`. Short-lived like the plain connector: the
toolset is closed when the context exits. No model is involved.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from google.adk.tools.mcp_tool import MCPToolset, StreamableHTTPConnectionParams

from acme.sim.client import Connector, caller_headers


class _AdkSession:
    """The one `ClientSession` method `SimClient` uses, backed by ADK tools."""

    def __init__(self, toolset: MCPToolset) -> None:
        self._toolset = toolset

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        tools = await self._toolset.get_tools()
        tool = next((t for t in tools if t.name == name), None)
        if tool is None:
            raise KeyError(f"unknown MCP tool: {name}")
        # `MCPTool` returns the raw MCP `CallToolResult` (isError, structuredContent).
        return await tool.run_async(args=arguments, tool_context=None)  # type: ignore[arg-type]


def adk_connector(base_url: str, caller: str, tokens: Mapping[str, str]) -> Connector:
    """ADK connector acting as ONE caller: every request carries only that caller's token (#27)."""

    @asynccontextmanager
    async def connect(server: str):
        headers = caller_headers(caller, base_url, tokens, server)
        toolset = MCPToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=f"{base_url.rstrip('/')}/mcp/{server}/", headers=headers
            )
        )
        try:
            yield _AdkSession(toolset)
        finally:
            await toolset.close()

    return connect  # type: ignore[return-value]
