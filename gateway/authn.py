"""Inbound authN (M2): JWT via JWKS, with a static-token bootstrap fallback.

The verifier implements the SDK's `TokenVerifier` protocol; the bearer
middleware calls it on every request and exposes the result as
`request.user.access_token` (see `AuthenticatedUser`).
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from mcp.server.auth.provider import AccessToken

log = logging.getLogger("gateway.authn")


class JWTTokenVerifier:
    """RS256 JWT verification against a JWKS endpoint (iss/aud/exp)."""

    def __init__(self, jwks_url: str, issuer: str, audience: str):
        self.jwks_url = jwks_url
        self.issuer = issuer
        self.audience = audience
        self._keys: dict[str, object] = {}

    async def prefetch(self, retries: int = 30, delay: float = 1.0) -> None:
        """Fetch JWKS at startup (retry: the IdP may boot after the gateway)."""
        for attempt in range(1, retries + 1):
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    r = await c.get(self.jwks_url)
                    r.raise_for_status()
                    self._load(r.json())
                    log.info("JWKS loaded from %s (%d keys)", self.jwks_url, len(self._keys))
                    return
            except Exception as e:  # noqa: BLE001
                log.warning("JWKS fetch attempt %d/%d failed: %s", attempt, retries, e)
                await asyncio.sleep(delay)
        raise ConnectionError(f"JWKS unavailable at {self.jwks_url}")

    def _load(self, jwks: dict) -> None:
        for k in jwks.get("keys", []):
            if k.get("kty") == "RSA" and k.get("kid"):
                self._keys[k["kid"]] = RSAAlgorithm.from_jwk(k)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
            key = self._keys.get(header.get("kid", ""))
            if key is None:
                log.warning("unknown kid %r", header.get("kid"))
                return None
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
            )
        except jwt.PyJWTError as e:
            log.info("token rejected: %s", e)
            return None
        return AccessToken(
            token=token,
            client_id=claims.get("client_id") or claims.get("sub", ""),
            subject=claims.get("sub"),
            scopes=claims.get("scope", "").split(),
            expires_at=claims.get("exp"),
            resource=str(claims.get("aud", "")),
            claims=claims,  # agent_id, iss, jti, ... ride along for M3 policy/audit
        )


class StaticTokenVerifier:
    """Bootstrap crutch for M1-style static tokens. Maps token -> user; the
    synthetic AccessToken carries agent_id='unknown'. Removed once OIDC is
    mandatory."""

    def __init__(self, tokens: dict[str, str]):
        self.tokens = tokens  # token -> user

    async def prefetch(self) -> None:
        return None

    async def verify_token(self, token: str) -> AccessToken | None:
        user = self.tokens.get(token)
        if not user:
            return None
        return AccessToken(
            token=token,
            client_id=user,
            subject=user,
            scopes=[],
            claims={"agent_id": "unknown", "authn": "static"},
        )


def identity_from(request) -> dict:
    """Extract the identity the bearer middleware attached to the Starlette
    request. Returns {'user': ..., 'agent': ...} or {} if unauthenticated
    (e.g. stdio transport, where ctx.request is not an HTTP request)."""
    user = getattr(request, "user", None) if request is not None else None
    access_token = getattr(user, "access_token", None)
    if access_token is None:
        return {}
    claims = access_token.claims or {}
    return {
        "user": access_token.subject or access_token.client_id,
        "agent": claims.get("agent_id", "unknown"),
    }
