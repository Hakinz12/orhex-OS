# ORHREX OS — Walrus & Tatum Integration Flow

## 1. Walrus Memory Mainnet Flow

ORHREX OS utilizes **Walrus Memory** to solve the persistent memory problem in AI agents. Walrus stores encrypted, verifiable memory blobs on decentralized storage nodes secured by Sui.

The backend adapter uses the official Python SDK, pinned to `memwal==0.2.1`.
It passes `MEMWAL_SERVER_URL` and the configured namespace directly to the SDK;
an explicit server URL takes precedence over an SDK environment preset. The
SDK expects a 64-character hexadecimal Ed25519 delegate key and a Sui account
ID formatted as `0x` plus 64 hexadecimal characters. The credentials remain
backend-only.

### The Memory Lifecycle:
1. **Extraction (LLM):** During chat, candidate atomic facts, commitments, and goals are extracted.
2. **Explicit User Consent:** The UI presents a "Save to Memory" action chip. Silent storage is disallowed.
3. **Signing & Dispatch (Backend):**
   - The FastAPI backend commits a `pending` memory operation, including its
     owner, per-user namespace, metadata, and stable idempotency key, before
     dispatching the write.
   - The SDK signs the text and namespace using the Ed25519 delegate private key (`MEMWAL_PRIVATE_KEY`).
   - The SDK sends the signed request to `MEMWAL_SERVER_URL`.
4. **State Confirmation:**
   - `remember_and_wait()` waits for the storage job to finish.
   - The adapter reports `CONFIRMED` only when the SDK returns a blob ID.
   - The app confirms the local operation only after that result is committed.
     Definite failures become `FAILED`; ambiguous outcomes become
     `RECONCILIATION_REQUIRED`.
   - Stale pending operations are reconciled by SDK job-status lookup when a
     job ID exists. They are never automatically resubmitted.
5. **Recall:**
   - In subsequent sessions, semantic search queries the user's namespace in Walrus Memory.
   - The returned memories are ranked by relevance and injected into the LLM context prompt with citation anchors.

MemWal 0.2.1 confirms writes through `remember_and_wait()` and supports
semantic recall. It does not expose direct blob-status lookup or memory
deletion; the adapter reports these operations as unsupported. The SDK write
method accepts text and namespace only, so category, memory type, and
additional metadata are not stored in Walrus by the SDK.

The application derives each user's namespace from the configured namespace
prefix and a one-way hash of the application user ID; clients do not choose a
namespace. A durable operation ID is passed as the SDK idempotency key. While
the SDK supports explicit idempotency keys and job-status lookup, uncertain
operations are not retried automatically: an operator or later recovery
workflow must first establish the original job's outcome. Local category,
memory type, source, confidence, metadata, owner, and timestamps remain in
`memory_sync_records`; they are not Walrus payload fields.

### API authentication and ownership

Memory and Walrus readiness endpoints require an `Authorization: Bearer`
credential. Tokens are generated with cryptographic randomness, stored only
as SHA-256 digests in `user_access_tokens`, and may be revoked or given an
expiry. Newly provisioned tokens have a mandatory configurable 24-hour
default lifetime (`AUTH_TOKEN_TTL_HOURS`); expiry and revocation are checked
on each authenticated request. The authenticated token's database user is the sole source of
ownership; request bodies do not accept `user_id` or `namespace`. User/token
provisioning is an administrative, out-of-band operation through
`python -m app.cli provision-user`; there is no public signup or token-issuance
route. The one-time token is displayed only to the operator. Run
`python -m app.cli revoke-user-tokens --user-id <id>` to revoke all active
credentials for an account.

This MVP does not provide password login, refresh tokens, automated identity
provider integration, or a token-management UI. Long-lived tokens must be
revoked out of band and sent only over HTTPS. The development frontend does
not yet include a sign-in flow. The browser API client must not hold bearer
tokens in `localStorage` or a `NEXT_PUBLIC_*` variable. For browser integration,
use a same-origin Next.js backend-for-frontend route that stores the credential
in an `HttpOnly; Secure; SameSite` cookie and forwards authenticated requests
server-side, with CSRF protection for state-changing requests. No frontend
cookie flow is implemented in this phase.

The API requires `API_PUBLIC_URL=https://...` and explicit HTTPS CORS origins
when `APP_ENV=production`. TLS must terminate at the deployment edge and
cleartext public access must be disabled there. `/api/v1/memory/*` requests
are rate-limited per direct client IP in each API process. Multi-worker or
multi-instance deployments need an edge/shared rate limiter for a global limit.

---

## 2. Tatum Sui RPC Integration Flow

ORHREX OS provides contextual on-chain intelligence through **Tatum's Sui RPC Gateway**.

### Guardrails & Security:
- **Strictly Read-Only:** The user only inputs a public Sui address (`0x...`). No private keys, mnemonic phrases, or signing capabilities are ever requested.
- **Tatum Gateway RPC:** The backend routes calls via `https://sui-mainnet.gateway.tatum.io` using the `x-api-key` header.
- **Supported Methods:**
  - `suix_getBalance`: Queries coin balance for SUI (`0x2::sui::SUI`).
  - `suix_queryTransactionBlocks`: Queries recent transaction activity.
  - `sui_getLatestCheckpointSequenceNumber`: Validates node liveness and sync status.
- **Explainable Interpretation:** Raw on-chain data is paired with user preferences to create clear explanations (e.g., notifying the user of contract activity relevant to an active goal).
