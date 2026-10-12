"""App wiring: backends + proxy server + Streamable HTTP surface."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .backends import Backend
from .proxy import build_gateway_server

log = logging.getLogger("gateway.app")


def parse_backends_env(raw: str) -> dict[str, str]:
    """'billing=http://127.0.0.1:9101/mcp,crm=http://127.0.0.1:9102/mcp'"""
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        alias, sep, url = pair.partition("=")
        if not sep:
            raise ValueError(f"bad GATEWAY_BACKENDS entry {pair!r}, want alias=url")
        out[alias.strip()] = url.strip()
    return out


def create_app(backend_urls: dict[str, str]) -> Starlette:
    backends = {alias: Backend(alias, url) for alias, url in backend_urls.items()}
    server = build_gateway_server(backends)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "backends": {
                    alias: {"url": be.url, "tools": [t.name for t in be.tools]}
                    for alias, be in backends.items()
                },
            }
        )

    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        custom_starlette_routes=[Route("/health", health, methods=["GET"])],
    )

    # Wrap the SDK app's lifespan so backends connect after the SDK's own
    # session manager starts, and close on shutdown.
    inner_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(starlette_app):
        async with inner_lifespan(starlette_app):
            for be in backends.values():
                await be.connect()
            log.info("gateway ready, %d backend(s) connected", len(backends))
            try:
                yield
            finally:
                for be in backends.values():
                    await be.close()

    app.router.lifespan_context = lifespan
    return app
