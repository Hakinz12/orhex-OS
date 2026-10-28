# ORHREX OS — Intelligence That Anticipates

[![Hackathon: Tatum x Walrus Total Recall 2026](https://img.shields.io/badge/Hackathon-Tatum_x_Walrus_Total_Recall_2026-blueviolet.svg)](https://tatum.io/hackathon/total-recall)
[![Track: Smart Agents for Real Life](https://img.shields.io/badge/Track-Smart_Agents_for_Real_Life-00C49F.svg)](https://recall-tatum-walrus.vercel.app/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

> **ORHREX OS** is a memory-powered personal AI operating system engineered to eliminate context fragmentation. Rather than resetting context every conversation, ORHREX carries an authorized, durable memory of goals, preferences, and commitments across sessions using **Walrus Memory Mainnet**. It connects persistent memory with live on-chain signals through **Tatum Sui RPC** and synthesizes proactive, explainable recommendations using **Google Gemini**.

---

## 🎯 The One-Line Demo
> *Tell ORHREX about a goal, preference, recurring commitment, or connected Sui wallet; return in a fresh session — ORHREX recalls the exact facts and surfaces a useful, explainable next action you never have to repeat.*

---

## 🏗️ System Architecture

ORHREX OS is built as a clean, decoupled **modular monolith**:

```
+--------------------------------------------------------------------------+
|                        Next.js 15 Web Frontend                           |
|       Dashboard  |  Contextual Chat  |  Memory Centre  |  Sui Insight    |
+------------------------------------+-------------------------------------+
                                     | REST (JSON)
+------------------------------------v-------------------------------------+
|                      FastAPI Orchestration Service                       |
|   - Authentication & Scope Validation                                    |
|   - Context Orchestrator & Anticipation Engine                           |
|   - Deterministic Rule Triggers & Citation Builder                       |
+--------+------------------+-------------------+-----------------+--------+
         |                  |                   |                 |
+--------v--------+ +-------v---------+ +-------v-------+ +-------v--------+
|   LLM Adapter   | | Walrus Adapter  | | Tatum Adapter | |   DB Adapter   |
| (Google Gemini) | |(Memory Mainnet) | |   (Sui RPC)   | | (Async SQL)    |
+--------+--------+ +-------+---------+ +-------+-------+ +-------+--------+
         |                  |                   |                 |
+--------v--------+ +-------v---------+ +-------v-------+ +-------v--------+
| Google Gemini   | | Walrus Storage  | | Sui Mainnet   | | PostgreSQL /   |
| 2.5 Flash       | | Mainnet Blobs   | | via Tatum     | | SQLite Async   |
+-----------------+ +-----------------+ +---------------+ +----------------+
```

### Core Architectural Principles
1. **Zero Silent Storage:** Memories are only committed after user confirmation.
2. **Asynchronous Truth:** Walrus writes transition through `queued -> confirmed -> failed`. The UI never claims persistence until verified by Walrus.
3. **Strict Credential Isolation:** All Walrus Ed25519 delegate keys and Tatum API credentials reside strictly on the FastAPI backend.
4. **Explainable Anticipation:** Every proactive recommendation cites its exact memory ID or live data source.
5. **Read-Only On-Chain Data:** Tatum Sui access is strictly read-only; no private keys or signing capabilities are requested.

---

## 📂 Repository Structure

```
orhrex-os/
├── apps/
│   └── web/                   # Next.js 15 + TypeScript + Tailwind CSS Frontend
│       ├── src/
│       │   ├── app/           # App Router (Dashboard, Chat, Memories, Sui)
│       │   ├── components/    # Reusable UI & Anticipation components
│       │   └── lib/           # Typed API client and TypeScript definitions
│       └── package.json
├── services/
│   └── api/                   # Python FastAPI Backend
│       ├── app/
│       │   ├── adapters/      # Modular adapters (LLM, Walrus, Tatum, DB)
│       │   ├── api/v1/        # API endpoints (health, chat, memories, etc.)
│       │   ├── core/          # Pydantic configuration & database engine
│       │   ├── models/        # SQLAlchemy ORM & Pydantic schemas
│       │   └── main.py        # Application entrypoint
│       ├── tests/             # Pytest automated test suite
│       └── requirements.txt
├── scripts/
│   ├── verify_integrations.py # Pre-flight credential & API handshake checker
│   └── seed_10_memories.py    # Mainnet memory seeding script
├── docs/                      # Architecture, API contracts, flow guides
├── .env.example               # Environment variables template
├── LICENSE                    # Apache 2.0 open-source license
└── README.md
```

---

## 🚀 Quick Start Guide

### Prerequisites
- **Python:** 3.11+ (Python 3.14 verified)
- **Node.js:** 18+ (Node v24 verified) & `npm`
- **Git**

### 1. Clone & Configure Environment
```bash
# Clone the repository
git clone https://github.com/<your-username>/orhrex-os.git
cd orhrex-os

# Copy environment template
cp .env.example .env
```
Open `.env` and fill in your API credentials:
- `GEMINI_API_KEY`: From [Google AI Studio](https://aistudio.google.com/)
- `TATUM_API_KEY`: From [Tatum Dashboard](https://dashboard.tatum.io/)
- `MEMWAL_*`: Walrus Memory Mainnet credentials from [Walrus](https://docs.wal.app/walrus-memory/)

### 2. Verify Integrations & Credentials
Run the non-destructive verification tool:
```bash
python scripts/verify_integrations.py
```

### 3. Backend Setup (FastAPI)
```bash
cd services/api

# Create and activate Python virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
# source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run backend tests
pytest -v

# Start FastAPI server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
Backend will be available at: `http://127.0.0.1:8000`  
Interactive Swagger docs: `http://127.0.0.1:8000/docs`

### 4. Frontend Setup (Next.js)
```bash
cd apps/web

# Install dependencies
npm install

# Start development server
npm run dev
```
Frontend will be available at: `http://localhost:3000`

---

## 🧪 Testing

Run backend automated tests:
```bash
cd services/api
pytest -v
```

---

## 🛡️ Security & Privacy Boundaries
- **No Custody:** ORHREX OS will never ask for or store wallet seed phrases or private keys.
- **Data Minimization:** No indiscriminate logging of chat conversations. Atomic facts are classified and stored with consent.
- **Isolated Namespaces:** User memories are partitioned strictly by authenticated user namespace.

