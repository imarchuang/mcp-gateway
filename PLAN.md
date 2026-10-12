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
| Kong AI MCP Proxy (GW 3.12+, per-tool ACL 3.13+) | Per-tool ACL as the core authZ unit; OpenAPI-driven REST→MCP proxying (M7) |
| AWS AgentCore Gateway | OIDC inbound auth; **Token Vault** = credential brokering via RFC 8693 token exchange; Cedar-style policies per tool |
| IBM ContextForge | Virtual servers = curated tool bundles; OTel tracing |
| Docker MCP Gateway | Simple file-based config; per-profile tool enable/disable |
| Willow/MintMCP (governance) | Approval step for dangerous calls; client admission |
| MCP spec 2026-07-28 + Security Best Practices | Streamable HTTP transport, session semantics; auth model: servers are OAuth resource servers, **token passthrough is an anti-pattern** |

## 2. Scope

### In scope (demo)

1. **Aggregation / virtual server**
   - one public MCP endpoint (Streamable HTTP) fronting N backend servers
   - tool namespacing: `billing__refund_payment` (server prefix) to avoid
     collisions; `list_tools` returns the union
2. **Inbound authN (OIDC)**
   - JWT validation via JWKS (iss/aud/exp); static bearer tokens only as
     an M1–M2 bootstrap crutch; production note: SSO/OIDC with
     authorization-code + PKCE
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
     decision, token_jti, latency_ms, result_status}` — args hashed, not
     stored (PII note); `token_jti` links the record into the identity
     chain (§2.9)
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
     injects it on outbound calls — the static-key ancestor of §2.9's
     OAuth2 brokering
9. **Credential brokering (OAuth2/OIDC outbound)**
   - the gateway never forwards the caller's token (token passthrough is
     an MCP-spec anti-pattern: aud mismatch → confused deputy); it holds
     its own IdP client identity and obtains per-backend tokens itself
   - default: **RFC 8693 token exchange** (on-behalf-of) — the backend
     receives `sub=<user>, act=mcp-gateway`; fallback: `client_credentials`
     M2M (user identity stops at the gateway)
   - backends validate their own tokens (aud + scope): **bypassing the
     gateway is useless**

### Out of scope

- Registry duties: approval workflow, ownership, lifecycle (that is
  `~/workspace_gh/mcp-registry`)
- Server hosting/provisioning (Docker MCP Gateway-style spawn)
- stdio bridging (stretch), human-approval async flow (stretch),
  GraphQL→MCP wrapping (REST→MCP is in scope, see §2.8)
- Interactive login UX: the demo IdP issues user tokens directly; the
  authorization-code + PKCE + consent dance is a production note, not
  demo scope

## 3. Architecture

```
MCP client (agent)
   │  Streamable HTTP, Authorization: Bearer <jwt_A: sub=alice, aud=gateway>
   ▼
┌─────────────────────────────────────────────────────────┐
│ mcp-gateway (single endpoint)                           │
│  1. authN   — JWT (JWKS) → {user, agent}                │
│  2. resolve — "billing__refund_payment" → backend, tool │
│  3. authZ   — policy[user,agent,tool] (registry-synced) │
│  4. rate    — token bucket per (identity, tool)         │
│  5. broker  — RFC 8693 exchange, inject outbound token  │
│  6. proxy   — JSON-RPC forward, session mgmt            │
│  7. guard   — PII-mask results                          │
│  8. audit   — append per-call record (incl. jti chain)  │
└───────────────┬───────────────────────┬─────────────────┘
                ▼                       ▼
        billing-mcp server        legacy-crm-api
        (real MCP server,         (FastAPI CRUD, wrapped
         own resource server)      via OpenAPI → tools)
                ▲
   policy sync: GET registry /v1/sync/approved?since= (30s poll)
   identity:    demo-idp issues jwt_A to agents; the gateway exchanges
                jwt_A → jwt_B per call; every side validates via JWKS
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
- **PyJWT** for JWT issuing/verification (demo IdP, gateway verifier,
  backend verifier); keys served as JWKS by the demo IdP
- Demo processes: `examples/billing-mcp` (MCP server **and** OAuth2
  resource server), `examples/legacy-crm-api` (REST CRUD for M7
  wrapping), `examples/demo-idp` (JWT issuer: `/token` + JWKS)

## 6. Milestones

- **M1 — proxy core**: single endpoint, one backend, namespaced
  `list_tools`/`call_tool` pass-through, session handling
- **M2 — inbound OIDC authN + multi-backend**: JWT validation via JWKS
  (SDK `TokenVerifier`/`AuthSettings`), `sub`+`agent_id` into request
  context (static bearer kept as bootstrap fallback); two backends,
  routing table, collision-safe namespacing
- **M3 — authZ + registry sync**: policy engine, `/v1/sync/approved`
  polling, risk-class default rules, last-synced fallback
- **M4 — credential brokering (OAuth2/OIDC)**: demo IdP (JWT issuer),
  TokenBroker (client_credentials + RFC 8693 token exchange,
  per-(backend, user, scope) caching), outbound token injection,
  billing-mcp as resource server, scope↔risk-class mapping
- **M5 — audit + rate limit**: audit store + viewer endpoint, token
  bucket; audit record gains the token `jti` chain
- **M6 — guardrails**: PII masking on results
- **M7 — REST→MCP wrapping**: legacy CRM CRUD service (FastAPI + SQLite,
  API-key protected), OpenAPI→tools adapter, operationId allowlist,
  method→risk heuristic, upstream API-key injection
- **M8 (stretch)**: OTel spans; `require_approval` via MCP elicitation;
  GraphQL→MCP

### M4 detail: credential brokering (OAuth2/OIDC)

Three OAuth roles: inbound, the gateway is a **resource server**
(validating the agent's JWT via JWKS); between gateway and IdP it is a
**broker**; outbound, it is an **OAuth client** — and every backend is
its own resource server.

Outbound modes (both implemented; passthrough mentioned only as the
anti-pattern):

| Mode | What happens | Verdict |
| --- | --- | --- |
| Token passthrough | agent's JWT forwarded verbatim | ❌ MCP Security Best Practices anti-pattern (aud mismatch → confused deputy) |
| Client credentials | gateway's own identity, `grant_type=client_credentials`, aud=backend | ✅ M2M fallback; user identity stops at the gateway |
| **Token exchange (RFC 8693)** | `subject_token`=agent JWT → new token aud=backend, `act`=gateway | ✅ default; `sub=alice, act=mcp-gateway` reaches the backend |

Sequence per call:

```
agent ──Bearer jwt_A (sub=alice, aud=gateway)──▶ gateway
  ① JWKS-validate jwt_A    ② policy check (M3)
  ③ POST IdP /token: grant_type=urn:ietf:params:oauth:grant-type:token-exchange
     subject_token=jwt_A, audience=billing-mcp, scope=billing:refund
     → jwt_B (sub=alice, act=mcp-gateway, aud=billing-mcp)
  ④ call_tool with Authorization: Bearer jwt_B
  ⑤ billing-mcp validates aud + scope itself (bypassing the gateway is useless)
  ⑥ audit records jwt_B.jti — chain user→agent→gateway→backend
```

Components:

- `examples/demo-idp` (~100 lines, FastAPI + PyJWT): `POST /token`
  (client_credentials + token-exchange grants), `/.well-known/jwks.json`;
  issues jwt_A for demo users directly (production note: OIDC
  authorization code + PKCE + consent)
- `TokenBroker` in the gateway: `token_for(backend, subject_jwt)`; cache
  keyed `(backend, sub_hash, scope)`, refresh at exp−30s; per-call header
  injection on the SDK client's own httpx client (tokens differ per user
  and expire — never set once at connect time); IdP outage → fail-closed
  on `dangerous`, cached tokens only for the rest
- backend config gains an `auth` block: `{type: oauth2_token_exchange |
  oauth2_client_credentials | api_key | none, token_url, client_id,
  client_secret, audience, scope}`
- `billing-mcp` becomes a resource server: validates jwt_B
  (`aud=billing-mcp`); `refund_payment` additionally requires scope
  `billing:refund`

Scope ↔ registry risk_class mapping (three vocabularies, one grant):

| risk_class (registry) | scope (IdP) | example tool |
| --- | --- | --- |
| read | `billing:read` | get_invoice |
| write | `billing:write` | — |
| dangerous | per-tool scope, e.g. `billing:refund` | refund_payment |

The registry records the risk class; the gateway policy (M3) translates
it into a scope requirement; the IdP only issues scopes that were
approved — each layer speaks its own language about the same
authorization.

### M7 detail: legacy CRM service & OpenAPI adapter

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
5. Identity-chain demo: calling billing-mcp **directly** with Alice's
   gateway token fails (`aud` mismatch — no passthrough); through the
   gateway, an allowed call arrives at the backend as
   `sub=alice, act=mcp-gateway`, and the audit record correlates both
   tokens' `jti`s
6. Registry deprecates v1.0.0 → next sync removes the tools from the
   gateway's catalog — **the registry decided in days; the gateway
   enforced in milliseconds**
7. Provenance demo: `crm__*` tools were built from
   `GET http://legacy-crm:9200/openapi.json` of a plain FastAPI CRUD
   service; `crm__delete_customer` (DELETE → `dangerous`) is denied by
   the same registry-sourced policy as any native MCP tool — the policy
   engine cannot tell wrapped REST from real MCP, and neither can the
   agent
8. `GET /audit` shows the full chain: user, agent, tool, decision,
   token `jti`, latency

## 8. Success criteria for the demo

- A client can only ever reach backend servers through the gateway —
  and going around it is useless: backends validate their own
  aud/scopes, no token passthrough
- Every allow/deny decision is explainable from (registry list + policy)
- The audit log alone answers "who ran this function, as whom, with what
  outcome" — the question a plain API gateway cannot answer — and the
  `jti` chain ties it to what the backend saw
