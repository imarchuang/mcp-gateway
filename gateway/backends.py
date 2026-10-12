"""Backend MCP server connections.

M1 session model: each backend gets ONE long-lived upstream ClientSession,
created at gateway startup and shared by all downstream client sessions.
Calls are serialized with a per-backend lock. Per-downstream-session upstream
affinity (session pass-through) is an M2+ concern; the demo backends are
stateless so sharing is safe here.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack

import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

log = logging.getLogger("gateway.backends")


class Backend:
    def __init__(self, alias: str, url: str):
        self.alias = alias
        self.url = url
        self.tools: list[types.Tool] = []
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()

    async def connect(self, retries: int = 30, delay: float = 1.0) -> None:
        """Open the upstream session (with retry, so the gateway can start
        before its backends) and snapshot the backend's tool list."""
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                self._stack = AsyncExitStack()
                streams = await self._stack.enter_async_context(
                    streamable_http_client(self.url)
                )
                session = await self._stack.enter_async_context(
                    ClientSession(streams[0], streams[1])
                )
                await session.initialize()
                self._session = session
                self.tools = (await session.list_tools()).tools
                log.info(
                    "backend %r connected (%s), %d tools: %s",
                    self.alias,
                    self.url,
                    len(self.tools),
                    [t.name for t in self.tools],
                )
                return
            except Exception as e:  # noqa: BLE001 - retry any connect failure
                last_err = e
                if self._stack:
                    await self._stack.aclose()
                    self._stack = None
                log.warning(
                    "backend %r connect attempt %d/%d failed: %s",
                    self.alias, attempt, retries, e,
                )
                await asyncio.sleep(delay)
        raise ConnectionError(f"backend {self.alias!r} unreachable: {last_err}")

    async def call(self, tool_name: str, arguments: dict) -> types.CallToolResult:
        if self._session is None:
            raise RuntimeError(f"backend {self.alias!r} not connected")
        async with self._lock:
            return await self._session.call_tool(tool_name, arguments)

    async def close(self) -> None:
        if self._stack:
            await self._stack.aclose()
            self._stack = None
            self._session = None
