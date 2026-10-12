"""Entrypoint: `python main.py` (or `uvicorn main:app`).

Config via env:
  GATEWAY_BACKENDS  'billing=http://127.0.0.1:9101/mcp,crm=http://...:9102/mcp'
  GATEWAY_PORT      default 8081
"""

import logging
import os

import uvicorn

from gateway.app import create_app, parse_backends_env

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

_backends = parse_backends_env(
    os.environ.get("GATEWAY_BACKENDS", "billing=http://127.0.0.1:9101/mcp")
)
app = create_app(_backends)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("GATEWAY_PORT", "8081")))
