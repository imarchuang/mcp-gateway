"""M1 e2e: real MCP client -> gateway -> billing backend, over two HTTP hops.

Starts both servers as uvicorn threads, then uses the official SDK client to
verify namespaced list_tools / call_tool pass-through.
"""

import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

BILLING_PORT, GATEWAY_PORT = 19101, 18081


def serve(app, port: int) -> uvicorn.Server:
    srv = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    threading.Thread(target=srv.run, daemon=True).start()
    return srv


def wait_ready(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(url, timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"{url} not ready")


async def client_checks() -> None:
    url = f"http://127.0.0.1:{GATEWAY_PORT}/mcp"
    async with streamable_http_client(url) as (read, write, *_):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            assert init.server_info.name == "mcp-gateway-demo", init.server_info

            tools = (await s.list_tools()).tools
            names = {t.name for t in tools}
            assert names == {"billing__get_invoice", "billing__refund_payment"}, names
            gi = next(t for t in tools if t.name == "billing__get_invoice")
            assert "invoice_id" in gi.input_schema["properties"], gi.input_schema
            assert gi.description.startswith("[billing]"), gi.description

            r = await s.call_tool("billing__get_invoice", {"invoice_id": "inv-1001"})
            assert not r.is_error, r
            text = r.content[0].text
            assert "Acme Ltd" in text, text

            r = await s.call_tool(
                "billing__refund_payment",
                {"payment_id": "pay-9", "amount": 12.5, "reason": "demo"},
            )
            assert not r.is_error and "refunded" in r.content[0].text, r

            # tool missing on a known backend -> backend returns is_error result
            r = await s.call_tool("billing__nope", {})
            assert r.is_error, r

            # unknown backend prefix / malformed name -> gateway JSON-RPC error
            for bad in ("crm__search_contacts", "no_prefix"):
                try:
                    await s.call_tool(bad, {})
                    raise AssertionError(f"{bad} should have raised")
                except Exception as e:
                    assert "unknown tool" in str(e), e


def main() -> None:
    from examples.billing_mcp_server import app as billing_app

    os.environ["GATEWAY_BACKENDS"] = f"billing=http://127.0.0.1:{BILLING_PORT}/mcp"
    from gateway.app import create_app, parse_backends_env

    gateway_app = create_app(parse_backends_env(os.environ["GATEWAY_BACKENDS"]))

    servers = [serve(billing_app, BILLING_PORT), serve(gateway_app, GATEWAY_PORT)]
    wait_ready(f"http://127.0.0.1:{GATEWAY_PORT}/health")
    deadline = time.time() + 20
    while time.time() < deadline:
        health = httpx.get(f"http://127.0.0.1:{GATEWAY_PORT}/health").json()
        if health["backends"]["billing"]["tools"]:
            break
        time.sleep(0.3)
    print("health:", health)

    asyncio.run(client_checks())
    for srv in servers:
        srv.should_exit = True
    print("M1 e2e: all assertions passed")


if __name__ == "__main__":
    main()
