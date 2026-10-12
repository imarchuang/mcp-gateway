"""Demo backend #2: crm-mcp, a tiny MCP server on :9102.

Exists for M2 (multi-backend routing + collision-safe namespacing): its
`status` tool has the same name as billing-mcp's. (M7 additionally wraps a
*REST* CRM service; this is the native-MCP counterpart.)
"""

import os

import uvicorn
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("crm-mcp", version="1.0.0")

CONTACTS = {
    "c-1": {"id": "c-1", "name": "Ada Lovelace", "email": "ada@example.com", "tier": "gold"},
    "c-2": {"id": "c-2", "name": "Alan Turing", "email": "alan@example.com", "tier": "silver"},
}


@mcp.tool(description="Search contacts by name substring.")
async def search_contacts(query: str) -> list[dict]:
    q = query.lower()
    return [c for c in CONTACTS.values() if q in c["name"].lower()]


@mcp.tool(description="Fetch one contact by id.")
async def get_contact(contact_id: str) -> dict:
    c = CONTACTS.get(contact_id)
    if c is None:
        return {"error": f"contact {contact_id!r} not found"}
    return c


@mcp.tool(description="Service status check.")
async def status() -> dict:
    return {"service": "crm-mcp", "ok": True}


app = mcp.streamable_http_app(streamable_http_path="/mcp")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("CRM_PORT", "9102")))
