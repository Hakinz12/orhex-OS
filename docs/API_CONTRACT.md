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

### `POST /api/v1/chat`
Sends a user message into the orchestrator. Retrieves relevant memories, builds context, requests response from Gemini, and detects candidate memories.

#### Request Body
```json
{
  "message": "I need to complete my portfolio before Friday. I have 90 minutes each evening.",
  "session_id": "sess_891823",
  "user_id": "usr_default"
}
```

#### Response `200 OK`
```json
{
  "answer": "I have noted that your portfolio is due this Friday and you have 90-minute evening blocks available. Would you like me to save this commitment to your memory so I can remind you and track progress?",
  "session_id": "sess_891823",
  "evidence": [
    {
      "kind": "memory",
      "reference": "mem_001",
      "summary": "User prefers evening work sessions"
    }
  ],
  "candidate_memories": [
    {
      "type": "commitment",
      "text": "Portfolio submission target is 2026-11-06; 90 minutes dedicated each evening.",
      "category": "commitments",
      "confidence": 0.95
    }
  ],
  "needs_confirmation": true
}
```

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

## 5. Blockchain Intelligence (Tatum Sui RPC)

### `POST /api/v1/integrations/sui-wallet`
Associates a public Sui address with the user profile for read-only tracking.

### `GET /api/v1/integrations/sui-wallet/activity`
Fetches on-chain balance and recent transaction digest summary via Tatum RPC.
