# ORHREX OS — API Contract Specification

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
  "app_name": "ORHREX OS",
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
      "endpoint": "https://sui-mainnet.gateway.tatum.io"
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

### `GET /api/v1/memories`
List stored memories for the active user namespace.

### `POST /api/v1/memories/confirm`
Explicit user confirmation to persist a candidate memory to Walrus Memory Mainnet.

#### Request Body
```json
{
  "text": "Portfolio submission target is 2026-11-06.",
  "category": "commitments",
  "type": "commitment",
  "user_id": "usr_default"
}
```

#### Response `202 Accepted`
```json
{
  "status": "confirmed",
  "memory_id": "mem_walrus_1829",
  "blob_id": "walrus_blob_9829312019",
  "stored_at": "2026-10-28T01:52:00Z"
}
```

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

