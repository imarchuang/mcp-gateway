"""Demo IdP: a tiny JWT issuer standing in for a real OIDC provider.

Endpoints:
  POST /token                     — issues tokens
      grant_type=urn:demo:user    — DEMO SHORTCUT: mint a user token directly
                                    (params: user, agent_id, scope, expires_in).
                                    Production note: authorization code + PKCE
                                    + consent, not this.
      grant_type=client_credentials — M2M tokens (params: client_id,
                                    client_secret, audience, scope). Used by
                                    the gateway's TokenBroker in M4.
  GET /.well-known/jwks.json      — public signing key (RS256)

One RSA keypair is generated at startup (restart = new keys = all tokens
invalid; fine for a demo).
"""

import os
import time
import uuid

import jwt
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException
from jwt.algorithms import RSAAlgorithm

PORT = int(os.environ.get("IDP_PORT", "9300"))
ISSUER = os.environ.get("IDP_ISSUER", f"http://127.0.0.1:{PORT}")
# Resource indicator (aud) of the gateway — tokens issued here are FOR the
# gateway; backends get their own aud via token exchange / client_credentials
# in M4.
GATEWAY_AUD = os.environ.get("IDP_GATEWAY_AUD", "mcp-gateway")

# Demo client registry for client_credentials (M4 uses these).
CLIENTS = {
    "mcp-gateway": {"secret": "gateway-secret", "scopes": ["billing:read", "billing:refund"]},
}

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "demo-key-1"
PUBLIC_JWK = RSAAlgorithm.to_jwk(_key.public_key(), as_dict=True)
PUBLIC_JWK.update({"kid": KID, "use": "sig", "alg": "RS256"})

app = FastAPI(title="demo-idp", version="0.1.0")


def mint(sub: str, aud: str, scope: str, expires_in: int, extra: dict) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": sub,
        "aud": aud,
        "iat": now,
        "nbf": now - 1,
        "exp": now + expires_in,
        "jti": str(uuid.uuid4()),
        "scope": scope,
        **extra,
    }
    return jwt.encode(claims, _key, algorithm="RS256", headers={"kid": KID})


@app.get("/.well-known/jwks.json")
def jwks() -> dict:
    return {"keys": [PUBLIC_JWK]}


@app.post("/token")
def token(
    grant_type: str = Form(...),
    user: str | None = Form(None),
    agent_id: str | None = Form(None),
    client_id: str | None = Form(None),
    client_secret: str | None = Form(None),
    audience: str | None = Form(None),
    scope: str = Form(""),
    expires_in: int = Form(3600),
) -> dict:
    if grant_type == "urn:demo:user":
        if not user:
            raise HTTPException(400, "urn:demo:user requires 'user'")
        tok = mint(
            sub=user,
            aud=audience or GATEWAY_AUD,
            scope=scope,
            expires_in=expires_in,
            extra={"agent_id": agent_id} if agent_id else {},
        )
        return {"access_token": tok, "token_type": "bearer", "expires_in": expires_in}

    if grant_type == "client_credentials":
        client = CLIENTS.get(client_id or "")
        if not client or client["secret"] != client_secret:
            raise HTTPException(401, "invalid_client")
        requested = scope.split() if scope else client["scopes"]
        granted = [s for s in requested if s in client["scopes"]]
        tok = mint(
            sub=client_id or "",
            aud=audience or GATEWAY_AUD,
            scope=" ".join(granted),
            expires_in=expires_in,
            extra={"client_id": client_id},
        )
        return {"access_token": tok, "token_type": "bearer", "expires_in": expires_in}

    raise HTTPException(400, f"unsupported_grant_type: {grant_type}")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "issuer": ISSUER}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
