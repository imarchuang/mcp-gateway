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

# easiest: one supervised process group in a single terminal
# (idp :9300, billing :9101, crm :9102, gateway :8081 with OIDC inbound;
#  logs in /tmp/mcp-gw-demo/*.log; Ctrl-C stops everything)
./scripts/dev_up.sh
# cleanup from another terminal: ./scripts/dev_down.sh
```

Manual per-terminal startup (what `dev_up.sh` does under the hood):

```bash
# terminal 1: demo IdP on :9300 (JWKS + token issuer)
.venv/bin/python examples/demo_idp.py

# terminal 2+3: demo backends (billing-mcp :9101, crm-mcp :9102)
.venv/bin/python examples/billing_mcp_server.py
.venv/bin/python examples/crm_mcp_server.py

# terminal 4: gateway on :8081, OIDC inbound
GATEWAY_JWKS_URL=http://127.0.0.1:9300/.well-known/jwks.json \
GATEWAY_JWT_ISS=http://127.0.0.1:9300 \
.venv/bin/python main.py
```

Client endpoint: `http://127.0.0.1:8081/mcp` (Streamable HTTP), all requests
need `Authorization: Bearer <token>` — JWT when `GATEWAY_JWKS_URL` is set,
else the static bootstrap tokens (`alice-token`, `bob-token`). Ops endpoint:
`GET /health` shows connected backends + discovered tools.

## Manual testing

With the stack up (`./scripts/dev_up.sh`), using
`examples/manual_client.py` (`list` / `call <tool> '<json-args>'`):

```bash
cd ~/workspace_gh/mcp-gateway

# 1) mint a user token (demo shortcut grant; production = auth code + PKCE)
export TOKEN=$(curl -s -X POST http://127.0.0.1:9300/token \
  -d "grant_type=urn:demo:user&user=alice&agent_id=kai" | jq -r .access_token)

# 2) list tools — expect 6: billing__* and crm__*, each with its own `status`
.venv/bin/python examples/manual_client.py list

# 3) call tools across both backends
.venv/bin/python examples/manual_client.py call billing__get_invoice '{"invoice_id": "inv-1001"}'
.venv/bin/python examples/manual_client.py call crm__search_contacts '{"query": "ada"}'
.venv/bin/python examples/manual_client.py call billing__status '{}'   # compare with crm__status

# 4) error semantics: unknown alias -> gateway JSON-RPC error;
#    unknown tool on a KNOWN backend -> backend is_error passes through
.venv/bin/python examples/manual_client.py call nope__x '{}'
.venv/bin/python examples/manual_client.py call crm__nope '{}'

# 5) authN negative paths: no token / garbage token -> 401 (client sees
#    MCPError -32603 wrapping the rejection)
TOKEN= .venv/bin/python examples/manual_client.py list
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8081/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

# 6) expired token (minted already-expired) -> rejected
TOKEN=$(curl -s -X POST http://127.0.0.1:9300/token \
  -d "grant_type=urn:demo:user&user=alice&expires_in=-10" | jq -r .access_token) \
  .venv/bin/python examples/manual_client.py list

# 7) watch the identity chain per call (user/agent from the JWT)
tail -f /tmp/mcp-gw-demo/gateway.log | grep call_tool

# 8) ops endpoint
curl -s http://127.0.0.1:8081/health -H "Authorization: Bearer $TOKEN" | jq
```

Things worth playing with:

- mint with a different `user=bob` and re-call — the gateway log identity
  follows the token (no per-user authorization yet; that is M3)
- without `jq`, mint with
  `... | .venv/bin/python -c "import json,sys; print(json.load(sys.stdin)['access_token'])"`
- restart only the IdP → previously minted tokens die (new RSA keypair per
  boot, see `examples/demo_idp.py` docstring)

## Automated tests

```bash
.venv/bin/python tests/test_m1.py   # proxy core e2e (static-token fallback)
.venv/bin/python tests/test_m2.py   # OIDC + multi-backend e2e
```

Both spin up IdP/backends/gateway on ephemeral ports — no manual setup.

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
