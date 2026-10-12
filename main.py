"""Entrypoint: `python main.py` (or `uvicorn main:app`).

Config via env:
  GATEWAY_BACKENDS  'billing=http://127.0.0.1:9101/mcp,crm=http://...:9102/mcp'
  GATEWAY_PORT      default 8081
  GATEWAY_JWKS_URL  e.g. http://127.0.0.1:9300/.well-known/jwks.json
                    (unset → static-token bootstrap fallback)
  GATEWAY_JWT_ISS   token issuer, e.g. http://127.0.0.1:9300
  GATEWAY_JWT_AUD   audience this gateway accepts, default 'mcp-gateway'
"""

import logging
import os

import uvicorn

from gateway.app import create_app, parse_backends_env

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

_backends = parse_backends_env(
    os.environ.get(
        "GATEWAY_BACKENDS",
        "billing=http://127.0.0.1:9101/mcp,crm=http://127.0.0.1:9102/mcp",
    )
)
app = create_app(
    _backends,
    jwks_url=os.environ.get("GATEWAY_JWKS_URL"),
    issuer=os.environ.get("GATEWAY_JWT_ISS"),
    audience=os.environ.get("GATEWAY_JWT_AUD", "mcp-gateway"),
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("GATEWAY_PORT", "8081")))
