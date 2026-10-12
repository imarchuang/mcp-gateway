"""Demo backend: billing-mcp, a tiny MCP server on :9101.

Matches the tools registered in the mcp-registry demo:
  get_invoice (read) / refund_payment (dangerous)
"""

import os

import uvicorn
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("billing-mcp", version="1.0.0")

INVOICES = {
    "inv-1001": {"id": "inv-1001", "customer": "Acme Ltd", "amount": 420.0, "status": "paid"},
    "inv-1002": {"id": "inv-1002", "customer": "Globex", "amount": 1337.5, "status": "open"},
}


@mcp.tool(description="Fetch one invoice by id.")
async def get_invoice(invoice_id: str) -> dict:
    inv = INVOICES.get(invoice_id)
    if inv is None:
        return {"error": f"invoice {invoice_id!r} not found"}
    return inv


@mcp.tool(description="Refund a payment. Irreversible.")
async def refund_payment(payment_id: str, amount: float, reason: str = "") -> dict:
    # Demo backend: pretend it happened. The gateway's authZ (M3) is what
    # actually stands between an agent and this call.
    return {"payment_id": payment_id, "refunded": amount, "reason": reason, "status": "refunded"}


app = mcp.streamable_http_app(streamable_http_path="/mcp")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("BILLING_PORT", "9101")))
