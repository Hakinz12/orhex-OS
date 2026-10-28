#!/usr/bin/env python3
"""
ORHREX OS — Pre-flight Integration & Credential Verification Tool
Performs non-destructive diagnostics on:
  1. Google Gemini API
  2. Tatum Sui RPC Gateway
  3. Walrus Memory Endpoint & Agent Config
  4. Relational Database Connectivity
"""
import sys
import os
import asyncio
from pathlib import Path

# Add services/api to python path
current_dir = Path(__file__).resolve().parent
repo_root = current_dir.parent
api_dir = repo_root / "services" / "api"
sys.path.insert(0, str(api_dir))

import httpx
from app.core.config import settings
from app.adapters.llm.gemini import GeminiAdapter
from app.adapters.walrus.memwal import MemWalAdapter
from app.adapters.tatum.sui_rpc import TatumSuiAdapter
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text


async def check_database() -> dict:
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("sqlite:///") and not db_url.startswith("sqlite+aiosqlite:///"):
        db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

    connect_args = {}
    if "sqlite" in db_url:
        connect_args["check_same_thread"] = False

    try:
        engine = create_async_engine(db_url, connect_args=connect_args)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return {"status": "PASS", "info": f"Connected ({'SQLite' if 'sqlite' in db_url else 'PostgreSQL'})"}
    except Exception as e:
        return {"status": "FAIL", "info": f"Error: {e}"}


async def main():
    print("=" * 72)
    print("  ORHREX OS — Integration & Credential Handshake Diagnostics")
    print(f"  App Environment: {settings.APP_ENV}")
    print(f"  Workspace Root:  {repo_root}")
    print("=" * 72)

    gemini = GeminiAdapter()
    walrus = MemWalAdapter()
    tatum = TatumSuiAdapter()

    print("\nRunning live connection checks...\n")

    # 1. Database
    db_res = await check_database()

    # 2. Gemini
    gemini_res = await gemini.check_readiness()

    # 3. Tatum Sui RPC
    tatum_res = await tatum.check_readiness()

    # 4. Walrus Memory
    walrus_res = await walrus.check_readiness()

    # Output table
    print(f"{'Integration':<20} | {'Status':<12} | {'Details'}")
    print("-" * 72)
    print(f"{'App Database':<20} | {db_res['status']:<12} | {db_res['info']}")

    g_status = "PASS" if gemini_res.get("status") == "ready" else "PENDING"
    g_info = f"Model: {gemini_res.get('model', settings.GEMINI_MODEL)}"
    if gemini_res.get("status") == "unconfigured":
        g_info += " (GEMINI_API_KEY not provided in .env)"
    elif gemini_res.get("status") == "ready":
        g_info += f" - {gemini_res.get('display_name', 'Available')}"
    print(f"{'Google Gemini API':<20} | {g_status:<12} | {g_info}")

    t_status = "PASS" if tatum_res.get("status") == "ready" else "PENDING"
    t_info = f"Network: {tatum_res.get('network', 'mainnet')}"
    if tatum_res.get("latest_checkpoint"):
        t_info += f" | Checkpoint: {tatum_res['latest_checkpoint']}"
    elif tatum_res.get("status") == "unconfigured":
        t_info += " (TATUM_API_KEY not provided in .env)"
    print(f"{'Tatum Sui RPC':<20} | {t_status:<12} | {t_info}")

    w_status = "PASS" if walrus_res.get("configured") else "PENDING"
    w_info = f"Server: {walrus_res.get('server_url')}"
    if not walrus_res.get("configured"):
        w_info += " (MEMWAL_AGENT_ID / PRIVATE_KEY not provided in .env)"
    print(f"{'Walrus Memory':<20} | {w_status:<12} | {w_info}")

    print("=" * 72)
    print("Diagnostics complete. If credentials show PENDING, populate them in .env.\n")


if __name__ == "__main__":
    asyncio.run(main())

