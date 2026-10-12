# mcp-gateway (demo)

An **MCP gateway** demo: one endpoint in front of many MCP servers — it sits
**in the runtime call path**, authenticates callers, authorizes each tool call
in milliseconds, routes to the right backend, and audits everything. It
enforces what `mcp-registry` approved. See [PLAN.md](PLAN.md).

## Status

- **M1 — proxy core** ✅ single Streamable-HTTP endpoint, one backend,
  `alias__tool` namespacing, `tools/list` union + `tools/call` pass-through,
  session handling
- **M2 — OIDC inbound authN + multi-backend** ✅ demo IdP (JWT issuer, JWKS),
  RS256 verification via SDK `TokenVerifier`, `sub`+`agent_id` in request
  context, two backends with collision-safe namespacing (static-token
  bootstrap fallback retained)
- M3 — authZ + registry sync (policy from `mcp-registry` `/v1/sync/approved`): planned
- M4 — credential brokering (OAuth2/OIDC outbound, RFC 8693 + demo IdP): planned
- M5/M6 — audit + rate limit / guardrails: planned
- M7 — REST→MCP wrapping (legacy FastAPI CRUD + OpenAPI adapter): planned
- M8 — stretch (OTel, elicitation approval, GraphQL→MCP): planned

## Run

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt

# terminal 1: demo IdP on :9300 (JWKS + token issuer)
.venv/bin/python examples/demo_idp.py

# terminal 2+3: demo backends (billing-mcp :9101, crm-mcp :9102)
.venv/bin/python examples/billing_mcp_server.py
.venv/bin/python examples/crm_mcp_server.py

# terminal 4: gateway on :8081, OIDC inbound
GATEWAY_JWKS_URL=http://127.0.0.1:9300/.well-known/jwks.json \
GATEWAY_JWT_ISS=http://127.0.0.1:9300 \
.venv/bin/python main.py

# mint a demo user token (jwt_A) and call through the gateway:
TOKEN=$(curl -s -X POST http://127.0.0.1:9300/token \
  -d "grant_type=urn:demo:user&user=alice&agent_id=kai" | jq -r .access_token)

# e2e tests (spin up everything on ephemeral ports, no manual setup):
.venv/bin/python tests/test_m1.py   # proxy core (static-token fallback)
.venv/bin/python tests/test_m2.py   # OIDC + multi-backend
```

Client endpoint: `http://127.0.0.1:8081/mcp` (Streamable HTTP), all requests
need `Authorization: Bearer <token>` — JWT when `GATEWAY_JWKS_URL` is set,
else the static bootstrap tokens (`alice-token`, `bob-token`). Ops endpoint:
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
