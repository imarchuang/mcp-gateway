# mcp-gateway — Demo Plan

An **MCP gateway** demo, following the industry consensus shape
(AWS AgentCore Gateway, Kong AI MCP Proxy, IBM ContextForge, Docker MCP
Gateway, Willow/MintMCP governance gateways):

> A gateway sits **in the runtime call path**: one endpoint in front of many
> MCP servers. It authenticates the caller, authorizes **each tool call** in
> milliseconds, routes to the right backend, and writes an audit record of
> who-did-what. It enforces the list the **registry** approved.

## 1. Industry anchors

| Reference | What we borrow |
| --- | --- |
| Kong AI MCP Proxy (GW 3.12+, per-tool ACL 3.13+) | Per-tool ACL as the core authZ unit; OpenAPI-driven REST→MCP proxying (M6) |
| AWS AgentCore Gateway | OIDC inbound auth; Cedar-style policies per tool/operation/parameter; interceptors |
| IBM ContextForge | Virtual servers = curated tool bundles; OTel tracing |
| Docker MCP Gateway | Simple file-based config; per-profile tool enable/disable |
| Willow/MintMCP (governance) | Approval step for dangerous calls; client admission |
| MCP spec 2026-07-28 | Streamable HTTP transport, session semantics the gateway must respect |

## 2. Scope

### In scope (demo)

1. **Aggregation / virtual server**
   - one public MCP endpoint (Streamable HTTP) fronting N backend servers
   - tool namespacing: `billing__refund_payment` (server prefix) to avoid
     collisions; `list_tools` returns the union
2. **Inbound authN**
   - bearer-token → identity map (demo); production note: OIDC/SSO JWT
   - agent identity + end-user identity both carried (`sub` = user,
     `agent_id` = agent) — audit needs the full chain
3. **Per-call, per-tool authZ**
   - policy: `identity × tool → allow|deny|require_approval`
   - policy source: **synced from mcp-registry** (approved servers, tool
     risk classes) + local overrides file
   - `dangerous` tools default-deny unless explicitly allowed
4. **Routing & session handling**
   - Streamable HTTP in, Streamable HTTP out; session id pass-through;
     per-backend connection pool
5. **Audit & observability**
   - per-call record: `{ts, user, agent, tool, server, args_hash,
     decision, latency_ms, result_status}` — args hashed, not stored
     (PII note)
   - `GET /audit` viewer endpoint; structured JSON logs; OTel spans
     (stretch)
6. **Rate limiting**
   - token bucket per `identity × tool` (in-memory, demo scale)
7. **Guardrails (minimal)**
   - naive PII masking (email/card regex) on tool **results** before they
     return to the client
8. **REST→MCP wrapping (OpenAPI-driven)**
   - one legacy REST CRUD service (`examples/legacy-crm-api`, FastAPI +
     SQLite) standing in for the existing API estate
   - gateway-side adapter: fetch `/openapi.json`, map operations → tools
     (`operationId` → tool name; path/query params + requestBody → one
     JSON Schema), execute calls via httpx; tools surface under the same
     `alias__tool` namespacing as real MCP backends
   - curated exposure: per-backend allowlist of `operationId`s (the
     Docker/Kong "enabled tools" pattern) — wrapping does NOT mean
     exposing every endpoint
   - HTTP-method → risk-class heuristic (GET=`read`, POST/PATCH=`write`,
     DELETE=`dangerous`) mirrors what the registry records
   - the legacy API requires an API key (`X-API-Key`); the gateway
     injects it on outbound calls — a first taste of credential brokering

### Out of scope

- Registry duties: approval workflow, ownership, lifecycle (that is
  `~/workspace_gh/mcp-registry`)
- Server hosting/provisioning (Docker MCP Gateway-style spawn)
- stdio bridging (stretch), human-approval async flow (stretch),
  GraphQL→MCP wrapping (REST→MCP is in scope, see §2.8)

## 3. Architecture

```
MCP client (agent)
   │  Streamable HTTP, Authorization: Bearer <user-token>
   ▼
┌─────────────────────────────────────────────────────────┐
│ mcp-gateway (single endpoint)                           │
│  1. authN   — token → {user, agent}                     │
│  2. resolve — "billing__refund_payment" → backend, tool │
│  3. authZ   — policy[user,agent,tool] (registry-synced) │
│  4. rate    — token bucket per (identity, tool)         │
│  5. proxy   — JSON-RPC forward, session mgmt            │
│  6. guard   — PII-mask results                          │
│  7. audit   — append per-call record                    │
└───────────────┬───────────────────────┬─────────────────┘
                ▼                       ▼
        billing-mcp server        legacy-crm-api
        (real MCP server)         (FastAPI CRUD, wrapped
                                   via OpenAPI → tools)
                ▲
   policy sync: GET registry /v1/sync/approved?since= (30s poll)
```

## 4. Policy model

```yaml
# local overrides, merged over registry-approved list
- identity: { user: alice }
  tool: billing__refund_payment      # risk_class=dangerous (from registry)
  decision: require_approval         # demo: returns elicitation error
- identity: { user: alice }
  tool: billing__get_invoice         # risk_class=read
  decision: allow
- identity: { agent: "*" }
  tool: "*::dangerous"               # class-wide rule
  decision: deny
```

Registry outage behavior: keep last synced policy (fail-closed on
`dangerous`, fail-open on `read`) — mirrors ContextHub/Kai "serve last
snapshot".

## 5. Tech choices

- **Python 3.12 + FastAPI** for the HTTP surface (uvicorn, async end to end)
- **Official Python MCP SDK (`mcp` / FastMCP)** for MCP client (to backends)
  and server (to agents) primitives — do not hand-roll JSON-RPC framing
- SQLite via stdlib `sqlite3` for the audit store; in-memory rate limiter
- Two demo backends: tiny FastMCP servers (`examples/billing-mcp`,
  `examples/crm-mcp`)

## 6. Milestones

- **M1 — proxy core**: single endpoint, one backend, namespaced
  `list_tools`/`call_tool` pass-through, session handling
- **M2 — authN + multi-backend**: token→identity, two backends, routing
  table, collision-safe namespacing
- **M3 — authZ + registry sync**: policy engine, `/v1/sync/approved`
  polling, risk-class default rules, last-synced fallback
- **M4 — audit + rate limit**: audit store + viewer endpoint, token bucket
- **M5 — guardrails**: PII masking on results
- **M6 — REST→MCP wrapping**: legacy CRM CRUD service (FastAPI + SQLite,
  API-key protected), OpenAPI→tools adapter, operationId allowlist,
  method→risk heuristic, upstream API-key injection
- **M7 (stretch)**: OTel spans; `require_approval` via MCP elicitation;
  GraphQL→MCP

### M6 detail: legacy CRM service & OpenAPI adapter

`examples/legacy-crm-api`: FastAPI + SQLite CRUD service on :9200,
`X-API-Key` required on every endpoint (stands in for any internal REST
service built years before MCP existed):

| REST endpoint | `operationId` | → tool | risk class |
| --- | --- | --- | --- |
| `GET /contacts?query=` | `search_contacts` | `crm__search_contacts` | read |
| `GET /contacts/{id}` | `get_contact` | `crm__get_contact` | read |
| `POST /contacts` | `create_contact` | `crm__create_contact` | write |
| `PATCH /contacts/{id}` | `update_contact` | `crm__update_contact` | write |
| `DELETE /contacts/{id}` | `delete_customer` | `crm__delete_customer` | dangerous |

Adapter mapping rules (gateway side):

- tool name = `operationId`; description = operation `summary`
- input schema = path params ∪ query params ∪ requestBody properties,
  merged into one `{"type": "object", ...}`; `required` = path params ∪
  body.required
- call: path params templated into the URL; remaining args go to query
  (GET) or JSON body (POST/PATCH/DELETE); JSON response → text content;
  non-2xx → MCP `is_error` result with status + body
- per-backend allowlist of `operationId`s; demo beat: start with all five
  exposed, then remove `delete_customer` from the allowlist and watch it
  vanish from `tools/list` without touching the service or the registry
- the gateway holds the service's API key in backend config and injects
  `X-API-Key` on every outbound call — the agent never sees it
  (credential brokering seed)

## 7. Demo script (end state)

1. Registry demo has `acme/billing-mcp` v1.0.0 approved (see
   mcp-registry PLAN §7); gateway syncs it
2. MCP client connects to **gateway only**, sees
   `billing__get_invoice`, `billing__refund_payment` (real MCP server)
   alongside `crm__search_contacts`, `crm__delete_customer` (legacy REST
   CRUD service wrapped via OpenAPI — no MCP server behind them)
3. `alice` calls `billing__get_invoice` → allowed, audited, result
   PII-masked
4. `alice` calls `billing__refund_payment` → denied by
   class-rule (`dangerous` default-deny) with a clear error; audit shows
   the denial
5. Registry deprecates v1.0.0 → next sync removes the tools from the
   gateway's catalog — **the registry decided in days; the gateway
   enforced in milliseconds**
6. Provenance demo: `crm__*` tools were built from
   `GET http://legacy-crm:9200/openapi.json` of a plain FastAPI CRUD
   service; `crm__delete_customer` (DELETE → `dangerous`) is denied by
   the same registry-sourced policy as any native MCP tool — the policy
   engine cannot tell wrapped REST from real MCP, and neither can the
   agent
7. `GET /audit` shows the full chain: user, agent, tool, decision,
   latency

## 8. Success criteria for the demo

- A client can only ever reach backend servers through the gateway
- Every allow/deny decision is explainable from (registry list + policy)
- The audit log alone answers "who ran this function, as whom, with what
  outcome" — the question a plain API gateway cannot answer
