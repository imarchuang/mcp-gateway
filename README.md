# mcp-gateway (demo)

An **MCP gateway** demo: one endpoint in front of many MCP servers — it sits
**in the runtime call path**, authenticates callers, authorizes each tool call
in milliseconds, routes to the right backend, and audits everything. It
enforces what `mcp-registry` approved. See [PLAN.md](PLAN.md).

## Status

- **M1 — proxy core** ✅ single Streamable-HTTP endpoint, one backend,
  `alias__tool` namespacing, `tools/list` union + `tools/call` pass-through,
  session handling
- M2 — authN + multi-backend: planned
- M3 — authZ + registry sync (policy from `mcp-registry` `/v1/sync/approved`): planned
- M4/M5/M6 — audit + rate limit / guardrails / stretch: planned

## Run

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt

# terminal 1: demo backend (billing-mcp on :9101)
.venv/bin/python examples/billing_mcp_server.py

# terminal 2: gateway on :8081
.venv/bin/python main.py
# GATEWAY_BACKENDS="billing=http://127.0.0.1:9101/mcp,crm=http://...:9102/mcp" .venv/bin/python main.py

# smoke/e2e test (spins up both on ephemeral ports, no manual setup):
.venv/bin/python tests/test_m1.py
```

Client endpoint: `http://127.0.0.1:8081/mcp` (Streamable HTTP). Ops endpoint:
`GET /health` shows connected backends + discovered tools.

## How M1 works

```
MCP client ──streamable-http──▶ gateway :8081/mcp ──streamable-http──▶ billing-mcp :9101/mcp
```

- **Namespacing**: backend tools surface as `billing__get_invoice`,
  `billing__refund_payment`; `tools/call` strips the `billing__` prefix and
  forwards to the owning backend. Unknown alias / malformed name → JSON-RPC
  `invalid params`; unknown tool on a *known* backend → backend's own
  `is_error` result passes through untouched.
- **Sessions**: client↔gateway sessions are managed by the SDK's
  `StreamableHTTPSessionManager` (stateful, `mcp-session-id`). Gateway↔backend
  uses one shared upstream session per backend, created at startup (with
  connect retry) and serialized by a lock. Per-downstream-session upstream
  affinity is an M2+ concern; demo backends are stateless.
- **Stack**: Python 3.12, official `mcp` SDK 2.x (low-level `Server` for the
  proxy core, `MCPServer` for the example backend), Starlette/uvicorn.
