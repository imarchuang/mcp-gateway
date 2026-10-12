"""Manual test client for the gateway (hand-testing companion).

Usage:
  TOKEN=$(curl -s -X POST http://127.0.0.1:9300/token \
    -d "grant_type=urn:demo:user&user=alice&agent_id=kai" | jq -r .access_token)
  export TOKEN

  python examples/manual_client.py list
  python examples/manual_client.py call billing__get_invoice '{"invoice_id": "inv-1001"}'
  python examples/manual_client.py call crm__search_contacts '{"query": "ada"}'

Env:
  GW     gateway MCP endpoint (default http://127.0.0.1:8081/mcp)
  TOKEN  bearer token; default 'alice-token' (static bootstrap fallback).
         Set TOKEN= (empty) to send no credentials (expect rejection).
"""

import asyncio
import json
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

GW = os.environ.get("GW", "http://127.0.0.1:8081/mcp")
TOKEN = os.environ.get("TOKEN", "alice-token")


async def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    http = httpx.AsyncClient(headers=headers, timeout=15)
    try:
        async with streamable_http_client(GW, http_client=http) as (read, write, *_):
            async with ClientSession(read, write) as s:
                init = await s.initialize()
                if cmd == "list":
                    print(f"connected: {init.server_info.name} v{init.server_info.version}")
                    for t in (await s.list_tools()).tools:
                        print(f"- {t.name:<28} {t.description}")
                elif cmd == "call":
                    name = sys.argv[2]
                    args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
                    res = await s.call_tool(name, args)
                    print(f"[{'ERROR' if res.is_error else 'ok'}] {name}")
                    for c in res.content:
                        print(getattr(c, "text", c))
                else:
                    sys.exit(__doc__)
    finally:
        await http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
