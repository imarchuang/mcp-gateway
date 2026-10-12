"""App wiring: backends + authN + proxy server + Streamable HTTP surface."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from mcp.server.auth.settings import AuthSettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .authn import JWTTokenVerifier, StaticTokenVerifier
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


def create_app(
    backend_urls: dict[str, str],
    *,
    jwks_url: str | None = None,
    issuer: str | None = None,
    audience: str = "mcp-gateway",
    static_tokens: dict[str, str] | None = None,
) -> Starlette:
    """Build the gateway app. AuthN: JWT via JWKS when `jwks_url` is given,
    otherwise the static-token bootstrap fallback (M1 style)."""
    if jwks_url:
        if not issuer:
            raise ValueError("issuer is required when jwks_url is set")
        verifier = JWTTokenVerifier(jwks_url, issuer, audience)
        issuer_url = issuer
        log.info("authN: OIDC JWT (jwks=%s, iss=%s, aud=%s)", jwks_url, issuer, audience)
    else:
        verifier = StaticTokenVerifier(static_tokens or {"alice-token": "alice", "bob-token": "bob"})
        issuer_url = "https://static-auth.demo.local"  # informational only
        log.info("authN: static tokens (bootstrap fallback)")

    # NB: the SDK only installs the BearerAuthBackend (which calls the
    # verifier) when `auth` is set — auth=None + verifier = everything 401s.
    # The URLs are informational in the demo: strict iss/aud checking happens
    # in JWTTokenVerifier itself.
    auth = AuthSettings(
        issuer_url=issuer_url,  # type: ignore[arg-type]
        resource_server_url=f"https://gateway.demo.local/{audience}",  # type: ignore[arg-type]
    )

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
        auth=auth,
        token_verifier=verifier,
        custom_starlette_routes=[Route("/health", health, methods=["GET"])],
    )

    # Wrap the SDK app's lifespan so backends connect after the SDK's own
    # session manager starts, and close on shutdown.
    inner_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(starlette_app):
        async with inner_lifespan(starlette_app):
            await verifier.prefetch()
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
