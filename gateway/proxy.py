"""The proxy core: one MCP endpoint aggregating N backends.

Tools are namespaced as ``<alias>__<tool>`` so identically-named tools on
different backends never collide. ``tools/list`` returns the union;
``tools/call`` strips the prefix and forwards to the owning backend.
"""

from __future__ import annotations

import logging

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError

from .backends import Backend

log = logging.getLogger("gateway.proxy")

SEPARATOR = "__"


def prefixed(alias: str, tool_name: str) -> str:
    return f"{alias}{SEPARATOR}{tool_name}"


def build_gateway_server(backends: dict[str, Backend]) -> Server:
    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        tools: list[types.Tool] = []
        for alias, be in backends.items():
            for t in be.tools:
                tools.append(
                    types.Tool(
                        name=prefixed(alias, t.name),
                        title=t.title,
                        description=f"[{alias}] {t.description or ''}".strip(),
                        input_schema=t.input_schema,
                        output_schema=t.output_schema,
                        annotations=t.annotations,
                    )
                )
        log.info("list_tools -> %d tools across %d backends", len(tools), len(backends))
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        alias, sep, tool_name = params.name.partition(SEPARATOR)
        if not sep or alias not in backends:
            raise MCPError(
                types.INVALID_PARAMS,
                f"unknown tool {params.name!r}; tools are namespaced "
                f"as '<server>{SEPARATOR}<tool>'",
            )
        backend = backends[alias]
        log.info("call_tool %s -> backend %r tool %r", params.name, alias, tool_name)
        # M1: pure pass-through of the backend's CallToolResult.
        return await backend.call(tool_name, dict(params.arguments or {}))

    return Server(
        "mcp-gateway-demo",
        version="0.1.0-m1",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
