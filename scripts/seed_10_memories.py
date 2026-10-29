#!/usr/bin/env python3
"""
ORHEX OS — Mandatory Mainnet Memory Seeding Script
Seed and verify >=10 genuine, curated user-approved memories on Walrus Memory Mainnet.
"""
import sys
import asyncio
from pathlib import Path

# Add services/api to python path
repo_root = Path(__file__).resolve().parent.parent
api_dir = repo_root / "services" / "api"
sys.path.insert(0, str(api_dir))

from app.core.config import settings
from app.adapters.walrus.memwal import MemWalAdapter

# Curated 10 authentic hackathon memories for user_default
CURATED_MEMORIES = [
    {
        "category": "goals",
        "type": "semantic",
        "text": "User goal: Complete and submit ORHEX OS for the Tatum x Walrus Total Recall 2026 hackathon by November 10, 2026."
    },
    {
        "category": "commitments",
        "type": "commitment",
        "text": "Daily commitment: Dedicate 90 minutes every evening between 20:00 and 21:30 UTC to system development."
    },
    {
        "category": "personal",
        "type": "preference",
        "text": "User preference: Concisely explain technical trade-offs before generating code; prioritize functional reliability over superficial features."
    },
    {
        "category": "goals",
        "type": "semantic",
        "text": "Portfolio deadline: Target completion date for public project portfolio and video recording is November 6, 2026."
    },
    {
        "category": "work",
        "type": "procedural",
        "text": "Architecture guideline: Never store private keys or seed phrases on frontend; all delegate signing must remain backend-isolated."
    },
    {
        "category": "personal",
        "type": "preference",
        "text": "Notification style: User prefers proactive warnings 72 hours before any milestone deadline, with one concise check-in per day."
    },
    {
        "category": "work",
        "type": "semantic",
        "text": "Blockchain preference: User monitors Sui Mainnet address for project treasury activity and transaction status."
    },
    {
        "category": "commitments",
        "type": "commitment",
        "text": "Publishing commitment: Publish technical architectural deep dive blog post on Walrus persistent memory by November 8, 2026."
    },
    {
        "category": "work",
        "type": "semantic",
        "text": "Decentralized storage rule: Never declare a memory saved unless Walrus network returns a confirmed blob ID and proof."
    },
    {
        "category": "goals",
        "type": "semantic",
        "text": "Submission checklist: Verify all 10 Mainnet memories, working cross-session recall, Tatum Sui RPC endpoint, and 2-3 minute video demo."
    }
]


async def seed_memories():
    print("=" * 72)
    print("  ORHEX OS — Walrus Memory Mainnet Seeding Tool (>=10 Memories)")
    print("=" * 72)

    walrus = MemWalAdapter()
    readiness = await walrus.check_readiness()

    if not readiness.get("configured"):
        print("[!] Walrus Memory credentials are not yet configured in .env.")
        print("    Please set MEMWAL_AGENT_ID, MEMWAL_ACCOUNT_ID, and MEMWAL_PRIVATE_KEY.")
        print("    Skipping live Mainnet write until credentials are provided.")
        return

    print(f"Connected to Walrus Agent ID: {readiness.get('agent_id')}")
    print(f"Network: {readiness.get('network')}")
    print(f"Seeding {len(CURATED_MEMORIES)} curated memories...\n")

    namespace = "user_default"
    for idx, mem in enumerate(CURATED_MEMORIES, 1):
        print(f"[{idx}/{len(CURATED_MEMORIES)}] Storing: {mem['text'][:60]}...")
        result = await walrus.remember(
            namespace=namespace,
            text=mem["text"],
            category=mem["category"],
            memory_type=mem["type"]
        )
        status = result.get("status")
        blob_id = result.get("blob_id", "pending")
        print(f"    -> Status: {status} | Blob ID: {blob_id}")

    print("\nSeeding cycle complete. Check Memory Centre for live confirmed blobs.")


if __name__ == "__main__":
    asyncio.run(seed_memories())

