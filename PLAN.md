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
| Kong AI MCP Proxy (GW 3.12+, per-tool ACL 3.13+) | Per-tool ACL as the core authZ unit; REST→MCP proxying (stretch) |
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

### Out of scope

- Registry duties: approval workflow, ownership, lifecycle (that is
  `~/workspace_gh/mcp-registry`)
- Server hosting/provisioning (Docker MCP Gateway-style spawn)
- stdio bridging (stretch), human-approval async flow (stretch),
  REST/GraphQL→MCP wrapping (stretch)

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
        billing-mcp server        crm-mcp server
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

- **TypeScript + Node 22 + Hono** for the HTTP surface
- **`@modelcontextprotocol/sdk`** for MCP client (to backends) and server
  (to agents) primitives — do not hand-roll JSON-RPC framing
- better-sqlite3 for the audit store; in-memory rate limiter
- Two demo backends: tiny FastMCP-style servers built on the same SDK
  (`examples/billing-mcp`, `examples/crm-mcp`)

## 6. Milestones

- **M1 — proxy core**: single endpoint, one backend, namespaced
  `list_tools`/`call_tool` pass-through, session handling
- **M2 — authN + multi-backend**: token→identity, two backends, routing
  table, collision-safe namespacing
- **M3 — authZ + registry sync**: policy engine, `/v1/sync/approved`
  polling, risk-class default rules, last-synced fallback
- **M4 — audit + rate limit**: audit store + viewer endpoint, token bucket
- **M5 — guardrails**: PII masking on results
- **M6 (stretch)**: OTel spans; one REST→MCP wrapped tool to show the
  Kong/AgentCore pattern; `require_approval` via MCP elicitation

## 7. Demo script (end state)

1. Registry demo has `acme/billing-mcp` v1.0.0 approved (see
   mcp-registry PLAN §7); gateway syncs it
2. MCP client connects to **gateway only**, sees
   `billing__get_invoice`, `billing__refund_payment`, `crm__search_contacts`
3. `alice` calls `billing__get_invoice` → allowed, audited, result
   PII-masked
4. `alice` calls `billing__refund_payment` → denied by
   class-rule (`dangerous` default-deny) with a clear error; audit shows
   the denial
5. Registry deprecates v1.0.0 → next sync removes the tools from the
   gateway's catalog — **the registry decided in days; the gateway
   enforced in milliseconds**
6. `GET /audit` shows the full chain: user, agent, tool, decision,
   latency

## 8. Success criteria for the demo

- A client can only ever reach backend servers through the gateway
- Every allow/deny decision is explainable from (registry list + policy)
- The audit log alone answers "who ran this function, as whom, with what
  outcome" — the question a plain API gateway cannot answer
