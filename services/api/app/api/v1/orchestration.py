"""Authenticated API for ORHEX's central decision cycle and run history."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_memory_user
from app.core.database import get_db
from app.models.domain import OrchestrationRun, User
from app.services.orchestration_engine import orchestration_payload, run_orchestration

router = APIRouter(prefix="/orchestration", tags=["Autonomous Orchestration"])


class OrchestrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID
    trigger: Literal["manual", "dashboard", "conversation", "scheduled"] = "manual"


@router.post("/run", status_code=200)
async def execute_decision_cycle(
    body: OrchestrationRequest,
    user: User = Depends(get_current_memory_user),
    db: AsyncSession = Depends(get_db),
):
    record = await run_orchestration(
        db, user_id=user.id, idempotency_key=str(body.idempotency_key),
        trigger=body.trigger,
    )
    return orchestration_payload(record)


@router.get("/latest")
async def latest_decision_cycle(
    user: User = Depends(get_current_memory_user),
    db: AsyncSession = Depends(get_db),
):
    record = await db.scalar(select(OrchestrationRun).where(
        OrchestrationRun.user_id == user.id,
    ).order_by(OrchestrationRun.created_at.desc()).limit(1))
    if record is None:
        raise HTTPException(404, "No orchestration run exists yet.")
    return orchestration_payload(record)


@router.get("/runs")
async def orchestration_history(
    limit: int = 20,
    user: User = Depends(get_current_memory_user),
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(limit, 100))
    records = list((await db.scalars(select(OrchestrationRun).where(
        OrchestrationRun.user_id == user.id,
    ).order_by(OrchestrationRun.created_at.desc()).limit(limit))).all())
    return [orchestration_payload(item) for item in records]
