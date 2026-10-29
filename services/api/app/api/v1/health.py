"""Health check and adapter diagnostic endpoints."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.config import settings
from app.core.database import get_db
from app.models.schemas import HealthResponse
from app.adapters.llm.gemini import GeminiAdapter
from app.adapters.walrus.memwal import MemWalAdapter
from app.adapters.tatum.sui_rpc import TatumSuiAdapter

router = APIRouter(tags=["Health & Diagnostics"])

# Singleton adapter instances for health checks
gemini_adapter = GeminiAdapter()
walrus_adapter = MemWalAdapter()
tatum_adapter = TatumSuiAdapter()


@router.get("/health", response_model=HealthResponse)
async def get_system_health(db: AsyncSession = Depends(get_db)):
    """
    Returns live operational status of ORHEX OS API and all connected adapters.
    Verifies database connectivity and credential configuration without leaking secrets.
    """
    # 1. Database Check
    db_status = {"status": "connected", "type": "sqlite" if "sqlite" in settings.DATABASE_URL else "postgresql"}
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = {"status": "error", "error": str(e)}

    # 2. Check Adapters
    gemini_health = await gemini_adapter.check_readiness()
    walrus_health = await walrus_adapter.check_readiness()
    tatum_health = await tatum_adapter.check_readiness()

    return HealthResponse(
        status="ok" if db_status["status"] == "connected" else "degraded",
        app_name=settings.APP_NAME,
        version="0.1.0",
        environment=settings.APP_ENV,
        timestamp=datetime.now(timezone.utc),
        adapters={
            "database": db_status,
            "gemini": gemini_health,
            "walrus": walrus_health,
            "tatum": tatum_health
        }
    )
