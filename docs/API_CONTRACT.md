# ORHEX OS — API Contract Specification

Base URL: `http://localhost:8000/api/v1`

All responses follow standard HTTP status codes and return `application/json` payloads.

---

## 1. System Health & Diagnostics

### `GET /api/v1/health`
Returns the operational health of the FastAPI service and diagnostics for configured adapters.

#### Response `200 OK`
```json
{
  "status": "ok",
  "app_name": "ORHEX OS",
  "version": "0.1.0",
  "environment": "development",
  "timestamp": "2026-10-28T01:50:00Z",
  "adapters": {
    "database": {
      "status": "connected",
      "type": "sqlite"
    },
    "gemini": {
      "status": "ready",
      "model": "gemini-2.5-flash"
    },
    "walrus": {
      "status": "ready",
      "network": "mainnet",
      "agent_configured": true
    },
    "tatum": {
      "status": "ready",
      "network": "mainnet",
      "endpoint": "https://sui-mainnet.gateway.tatum.io",
      "connected": true
    }
  }
}
```

---

## 2. Contextual Chat & Intelligence

### `POST /api/v1/conversations`
Sends a message to Gemini and persists the user/assistant exchange in a bounded
conversation history. It retrieves bounded, owner-scoped Walrus memories for
contextual grounding but does not save or modify memories.

#### Request Body
```json
{
  "message": "A message of at most 4,000 characters",
  "conversation_id": null,
  "idempotency_key": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
}
```

`conversation_id` is omitted or `null` to start a conversation. Subsequent
messages include the returned identifier. The idempotency key is reused for a
retry of the same request. Client-provided owner IDs and namespaces are not
accepted. The API supports either the existing visitor credential or PAT
authenticator; visitor and PAT conversations have separate ownership scopes.

#### Response `200 OK`
```json
{
  "conversation_id": "conversation UUID",
  "version": 1,
  "messages": [
    {"id": "message UUID", "role": "user", "content": "Submitted message", "created_at": "UTC timestamp"},
    {"id": "message UUID", "role": "assistant", "content": "Gemini-generated response", "created_at": "UTC timestamp"}
  ],
  "memory_context": {"status": "retrieved", "count": 2}
}
```

`GET /api/v1/conversations/{conversation_id}` returns the latest 40 messages for
that same authenticated owner scope. Gemini receives at most the latest 20
messages and 24,000 characters of prior context; each incoming message is
limited to 4,000 characters, and each conversation stores at most 100 exchanges.
Concurrent writes use a database version check; a stale write returns `409`.
Retries with the same idempotency key return the already-persisted exchange.
Memory recall is performed through the authenticated owner's existing namespace
and filters local Trash/deletion-in-progress records. The API returns only a
truthful retrieval status (`retrieved`, `empty`, `unavailable`, or
`not_retrieved`) and a bounded item count; it never exposes memory IDs, raw
records, or namespace values. Retrieved text is bounded to five items and 10,000
characters total. Recall failure does not block conversation generation and
does not authorize unsupported claims of personal recall.
Missing Gemini credentials/provider availability return a safe `503`, provider
timeouts return `504`, and malformed model responses return `502`. Failed
provider calls do not fabricate or persist assistant replies.

The Next.js app uses same-origin `/api/bff/conversations` and
`/api/bff/conversations/{conversation_id}` routes. Conversation history remains
separate from permanent Walrus memory; no conversation message is automatically
saved as a memory.

---

## 3. Persistent Memory Operations (Walrus Memory)

The current authenticated API is rooted at `/api/v1/memory`. Ownership and the
per-user Walrus namespace are derived from the bearer-token principal; clients
must not submit an owner ID or namespace.

- `POST /remember` stores an explicitly submitted memory and returns its
  durable operation state.
- `POST /recall` performs semantic recall in the authenticated user's
  namespace. The backend removes results whose locally owned blobs are in
  Trash or have permanent deletion in progress before returning them.
- `GET /records/active` and `GET /records/trash` list confirmed memories from
  the corresponding owner-scoped folder.
- `DELETE /operations/{operation_id}` moves an owned confirmed memory to
  recoverable Trash. It does not delete the Walrus blob.
- `POST /operations/{operation_id}/restore` restores an owned trashed memory
  unless permanent deletion has started.
- `POST /operations/{operation_id}/permanent-delete` checks the existing
  Walrus capability. It currently returns `501 unsupported_operation`; it
  does not alter local state or delete remote data.

Trash is user-controlled by default. The nullable
`users.memory_trash_retention_days` field reserves `30` for a future opt-in
auto-empty policy; no automatic deletion job exists in this phase.

---

## 4. Anticipation & Daily Briefing

### `GET /api/v1/briefing`
Synthesizes today's active priorities, approaching deadlines, and suggested focus areas.

### `POST /api/v1/anticipation/run`
Evaluates deterministic rule triggers (deadlines < 72h, goal inactivity) against stored context.

---

## 5. Sui Read-Only API (Phase 8.3B-1)

The wallet-balance and owned-object routes below are implemented by ORHEX.
The network, health, and transaction routes remain contract-only and are not
implemented or mounted.

All wallet data is public chain data. Both implemented routes require an
ORHEX bearer token (`Authorization: Bearer orh_...`), but the supplied wallet
address is not bound to the authenticated user. Connecting a wallet does not
prove ownership; no signature challenge is performed. Tatum credentials stay
server-side and are never accepted from callers. Response fields are
ORHEX-normalized, not raw provider JSON.

### Shared validation and pagination

- Address path values must be `0x`-prefixed hexadecimal strings with 1–64
  hexadecimal digits. Short Sui address forms are accepted; callers are not
  required to pad to 64 digits.
- Transaction digests must be valid Base58 values which decode to exactly
  32 bytes, the Sui digest format.
- Balance and object-list pagination use optional opaque `cursor` and `limit`
  query parameters. `limit` defaults to 20 and is bounded to 1–50. The cursor is
  not interpreted by ORHEX; its format is provider-defined and capped at 512
  characters. Each request fetches at most one page; ORHEX does not
  automatically fetch subsequent pages.
- Paginated responses include `pagination.next_cursor` and
  `pagination.has_next_page`.

### `GET /api/v1/sui/network`

Proposed contract only; this route is not yet implemented.

```json
{
  "network": "mainnet",
  "endpoint": "https://sui-mainnet.gateway.tatum.io",
  "chain_identifier": "4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S"
}
```

The current Tatum adapter reads the GraphQL `chainIdentifier`; official Tatum
Sui RPC documentation also lists `sui_getChainIdentifier`. The exact provider
transport used by a future route remains an implementation decision.

### `GET /api/v1/sui/health`

Proposed contract only; this route is not yet implemented.

```json
{
  "status": "ready",
  "configured": true,
  "connected": true,
  "network": "mainnet",
  "endpoint": "https://sui-mainnet.gateway.tatum.io",
  "latest_checkpoint": 12345,
  "error": null,
  "message": null
}
```

Statuses are `unconfigured`, `ready`, `unreachable`, or `degraded`. The
current adapter produces these readiness fields and checks the latest
checkpoint.

### `GET /api/v1/sui/wallet/{address}/balances`

Returns one page of SUI and other coin balances with integer raw units and
decimal-safe strings. `amount` and `decimals` are null when provider coin
metadata is unavailable; `symbol` is nullable. Amounts are never represented
using binary floating-point. Pagination is included in the response.

```json
{
  "sui_address": "0x2",
  "network": "mainnet",
  "balances": [
    {
      "coin_type": "0x2::sui::SUI",
      "raw_balance": "1500000000",
      "amount": "1.5",
      "decimals": 9,
      "symbol": "SUI"
    }
  ],
  "pagination": {
    "next_cursor": null,
    "has_next_page": false
  }
}
```

Tatum GraphQL operation: `address(address: $address) { balances(first:
$first, after: $after) }`. The selection uses verified `coinType.repr`,
`totalBalance`, optional `coinMetadata.decimals` and `coinMetadata.symbol`,
and `pageInfo.hasNextPage/endCursor`. Raw integer balances remain strings.

### `GET /api/v1/sui/wallet/{address}/objects`

Returns one bounded page of normalized owned-object summaries. Query
parameters are optional `cursor` and `limit`.

```json
{
  "sui_address": "0x2",
  "network": "mainnet",
  "objects": [
    {
      "object_id": "0x3",
      "version": "1",
      "digest": "11111111111111111111111111111111",
      "type": "0x2::example::Item"
    }
  ],
  "pagination": {
    "next_cursor": null,
    "has_next_page": false
  }
}
```

The adapter calls GraphQL `address(address: $address) { objects(first:
$first, after: $after, filter: { owner: $address }) }`, selecting verified
`address`, `version`, `digest`, `contents.type.repr`, and
`pageInfo.hasNextPage/endCursor` fields. The provider cursor is passed through
as an opaque value.

### Same-origin frontend BFF routes

The Next.js BFF exposes corresponding browser-facing GET routes:

- `GET /api/sui/wallet/{address}/balances`
- `GET /api/sui/wallet/{address}/objects`

Both require the existing ORHEX session cookie. The cookie is `HttpOnly`; the
BFF reads it on the server and forwards its bearer token only to the matching
allowlisted `/api/v1/sui/wallet/{address}/{balances|objects}` backend route.
The browser never receives the bearer token or Tatum credentials. These are
read-only requests, so they do not use the state-changing-route same-origin
CSRF check.

The BFF accepts the same optional `limit` (1–50, backend default 20) and
opaque `cursor` parameters, rejects unknown or repeated query parameters, and
validates/encodes the address before forwarding. It exposes only fields from
the normalized balance/object response schemas. Upstream error bodies are
replaced with safe messages; `502` and `503` statuses are preserved, and
network failures return `503`.

### `GET /api/v1/sui/transaction/{digest}`

Proposed contract only; this route is not yet implemented.

```json
{
  "digest": "11111111111111111111111111111111",
  "timestamp_ms": 1767323045000,
  "status": "success"
}
```

Tatum's official Sui method list includes `sui_getTransactionBlock`. The
current adapter already normalizes `digest`, `timestamp_ms`, and `status` for
recent activity. Tatum-specific transaction-detail fields and their
availability are not yet verified; fields unavailable from the provider
remain optional rather than fabricated.

### Provider mapping and verification status

The following are ORHEX routes, not Tatum HTTP paths. The current adapter
uses the Tatum Sui GraphQL endpoint `${TATUM_SUI_RPC_URL}/graphql` with the
server-side `x-api-key` header. It does not use legacy JSON-RPC methods.

| ORHEX route | Candidate Tatum method | Verification |
|---|---|---|
| `GET /api/v1/sui/network` | `sui_getChainIdentifier` | Method listed by Tatum; existing adapter uses GraphQL `chainIdentifier`. |
| `GET /api/v1/sui/health` | `sui_getLatestCheckpointSequenceNumber` or `sui_getCheckpoint` | Methods listed by Tatum; current adapter checks a GraphQL checkpoint query. |
| `GET /api/v1/sui/wallet/{address}/balances` | GraphQL `Address.balances(first, after)` | Implemented; fields and cursor metadata listed above. |
| `GET /api/v1/sui/wallet/{address}/objects` | GraphQL `Address.objects(first, after, filter)` | Implemented; fields and cursor metadata listed above. |
| `GET /api/v1/sui/transaction/{digest}` | `sui_getTransactionBlock` | Method listed; Tatum-specific detail fields remain unverified. |

Provider references:

- [Tatum Sui RPC reference](https://docs.tatum.io/reference/rpc-sui)
- [Tatum authentication](https://docs.tatum.io/docs/authentication) (`x-api-key` is recommended)
- [Mainnet gateway discovery](https://sui-mainnet.gateway.tatum.io/_methods)
- [Mainnet gateway interface details](https://sui-mainnet.gateway.tatum.io/llms.txt)

The provider schema was verified through the live mainnet GraphQL gateway.
Legacy JSON-RPC references are not used by these routes. No wallet
transaction-history route is included in this contract.

Malformed address, limit, or cursor inputs return `422`. Missing provider
configuration and provider timeouts/connection failures return `503`;
provider HTTP, GraphQL, and malformed-response failures return `502`. Errors
never include upstream bodies, API keys, or credentials:
```json
{
  "error": "provider_unavailable",
  "message": "Sui provider is currently unavailable.",
  "operation_id": null
}
```

### Visitor sessions and memory BFF

The Next.js app supports the existing PAT session and an isolated anonymous
visitor session. `GET /api/session` returns safe metadata for the active session
(`kind: "pat"` or `kind: "visitor"`); visitor responses omit the internal User
ID and all credential material. The existing `POST` and `DELETE /api/session`
PAT sign-in/logout contract remains in place.

The same-origin visitor session BFF uses:

- `POST /api/session/visitor` to reuse a valid PAT/visitor session or bootstrap
  a visitor when one is not active.
- `PATCH /api/session/visitor` to rotate the current visitor credential.
- `PUT /api/session/visitor` to explicitly reset to a new isolated identity.
- `DELETE /api/session/visitor` to revoke the current visitor credential.

The BFF keeps the PAT cookie (`orhrex_session`, production
`__Host-orhrex_session`) separate from the visitor cookie
(`orhrex_visitor_session`, production
`__Host-orhrex_visitor_session`). Both are `HttpOnly`, `SameSite=Lax`, path `/`,
and host-only; production cookies are `Secure`. The visitor browser cookie is
limited to one year even though the backend visitor identity and credential
have no scheduled expiry. Raw visitor credentials are only returned by the
backend to the BFF, which stores them in the HttpOnly cookie and never includes
them in browser JSON.

Memory BFF requests select one cookie, preferring the PAT cookie when present,
and forward one bearer credential to the existing owner-scoped memory API.
PAT and visitor credentials are resolved only by their matching backend
authenticators. Sui BFF routes continue to use their existing PAT-authenticated
backend behavior.

All state-changing session and memory BFF requests require a matching `Origin`.
When `ORHEX_APP_ORIGIN` is configured, it is the canonical origin used for the
comparison. Production deployments must configure `ORHEX_APP_ORIGIN` to the
public HTTPS application origin. The BFF does not derive trust from arbitrary
`X-Forwarded-Host` or `X-Forwarded-Proto` values; the deployment proxy must
route the configured public origin to the Next.js app without relying on those
headers for the application's CSRF decision.
