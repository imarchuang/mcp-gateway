"""M2 e2e: OIDC inbound authN + multi-backend routing.

Processes (all in-thread): demo-idp (:19300), billing-mcp (:19101),
crm-mcp (:19102), gateway (:18081, two backends, JWT verification).
"""

import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("IDP_PORT", "19300")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

IDP_PORT, BILLING_PORT, CRM_PORT, GATEWAY_PORT = 19300, 19101, 19102, 18081
IDP = f"http://127.0.0.1:{IDP_PORT}"


def serve(app, port: int) -> uvicorn.Server:
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    return srv


def wait_ready(url: str, timeout: float = 20.0, headers: dict | None = None) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(url, timeout=1, headers=headers or {})
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"{url} not ready")


def mint(user: str, agent: str = "kai", **extra) -> str:
    r = httpx.post(
        f"{IDP}/token",
        data={"grant_type": "urn:demo:user", "user": user, "agent_id": agent, **extra},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def gw_client(token: str | None):
    """Context manager: an MCP client connection to the gateway, optionally
    carrying a bearer token. Enter+exit always happen in the same task."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    http = httpx.AsyncClient(headers=headers, timeout=10)
    return streamable_http_client(
        f"http://127.0.0.1:{GATEWAY_PORT}/mcp", http_client=http
    ), http


async def expect_rejected(token: str | None, label: str) -> None:
    """Auth failures surface at the client as a connection/initialize error:
    the bearer middleware returns HTTP 401 and the SDK client wraps the
    non-2xx response in MCPError(-32603, 'Server returned an error response')."""
    from mcp.shared.exceptions import MCPError

    cm, http = gw_client(token)
    try:
        async with cm as (read, write, *_):
            async with ClientSession(read, write) as s:
                await s.initialize()
        raise AssertionError(f"{label}: connection should have been rejected")
    except Exception as e:
        # anyio wraps the SDK's MCPError in nested ExceptionGroups — walk
        # to the leaves before matching.
        leaves = []

        def walk(exc: BaseException) -> None:
            if isinstance(exc, BaseExceptionGroup):
                for sub in exc.exceptions:
                    walk(sub)
            else:
                leaves.append(exc)

        walk(e)
        rejected = any(
            isinstance(leaf, MCPError) and leaf.error.code == -32603
            or any(s in str(leaf) for s in ("401", "Unauthorized", "403"))
            for leaf in leaves
        )
        assert rejected, (label, e)
    finally:
        await http.aclose()


async def client_checks() -> None:
    # 1) no token / garbage token / expired token -> rejected
    await expect_rejected(None, "no token")
    await expect_rejected("garbage.token.here", "garbage token")
    expired = mint("alice", expires_in=-10)
    await expect_rejected(expired, "expired token")

    # 2) valid token -> full session
    cm, http = gw_client(mint("alice", agent_id="kai"))
    try:
        async with cm as (read, write, *_):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.server_info.name == "mcp-gateway-demo", init.server_info

                tools = (await session.list_tools()).tools
                names = {t.name for t in tools}
                expected = {
                    "billing__get_invoice", "billing__refund_payment", "billing__status",
                    "crm__search_contacts", "crm__get_contact", "crm__status",
                }
                assert names == expected, names

                # 3) multi-backend routing
                r = await session.call_tool("crm__search_contacts", {"query": "ada"})
                assert "Ada Lovelace" in r.content[0].text, r.content[0].text

                # 4) collision-safe namespacing: same tool name on both backends
                r1 = await session.call_tool("billing__status", {})
                r2 = await session.call_tool("crm__status", {})
                assert "billing-mcp" in r1.content[0].text, r1.content[0].text
                assert "crm-mcp" in r2.content[0].text, r2.content[0].text
    finally:
        await http.aclose()


def main() -> None:
    from examples.billing_mcp_server import app as billing_app
    from examples.crm_mcp_server import app as crm_app
    from examples.demo_idp import app as idp_app
    from gateway.app import create_app

    gateway_app = create_app(
        {
            "billing": f"http://127.0.0.1:{BILLING_PORT}/mcp",
            "crm": f"http://127.0.0.1:{CRM_PORT}/mcp",
        },
        jwks_url=f"{IDP}/.well-known/jwks.json",
        issuer=IDP,
        audience="mcp-gateway",
    )

    servers = [
        serve(idp_app, IDP_PORT),
        serve(billing_app, BILLING_PORT),
        serve(crm_app, CRM_PORT),
        serve(gateway_app, GATEWAY_PORT),
    ]
    wait_ready(f"{IDP}/health")
    wait_ready(f"http://127.0.0.1:{GATEWAY_PORT}/health",
               headers={"Authorization": f"Bearer {mint('healthcheck')}"})
    deadline = time.time() + 20
    while time.time() < deadline:
        h = httpx.get(
            f"http://127.0.0.1:{GATEWAY_PORT}/health",
            headers={"Authorization": f"Bearer {mint('healthcheck')}"},
        ).json()
        if all(b["tools"] for b in h["backends"].values()):
            break
        time.sleep(0.3)
    print("health:", h)

    asyncio.run(client_checks())
    for srv in servers:
        srv.should_exit = True
    print("M2 e2e: all assertions passed")


if __name__ == "__main__":
    main()
