# ORHREX OS — Walrus & Tatum Integration Flow

## 1. Walrus Memory Mainnet Flow

ORHREX OS utilizes **Walrus Memory** to solve the persistent memory problem in AI agents. Walrus stores encrypted, verifiable memory blobs on decentralized storage nodes secured by Sui.

### The Memory Lifecycle:
1. **Extraction (LLM):** During chat, candidate atomic facts, commitments, and goals are extracted.
2. **Explicit User Consent:** The UI presents a "Save to Memory" action chip. Silent storage is disallowed.
3. **Signing & Dispatch (Backend):**
   - The FastAPI backend prepares the memory payload.
   - It signs the request using the agent's Ed25519 delegate private key (`MEMWAL_PRIVATE_KEY`).
   - The signed request is sent to the Walrus Memory endpoint (`MEMWAL_SERVER_URL`).
4. **State Confirmation:**
   - Initial state: `QUEUED`.
   - Once the Walrus storage network returns the blob ID and certificate, status transitions to `CONFIRMED`.
   - The app logs the sync record in the local database and returns the confirmed blob reference to the frontend.
5. **Recall:**
   - In subsequent sessions, semantic search queries the user's namespace in Walrus Memory.
   - The returned memories are ranked by relevance and injected into the LLM context prompt with citation anchors.

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

