"""Owner-scoped anticipation insight API."""
import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_memory_user
from app.core.database import get_db
from app.models.domain import AnticipationInsight, User
from app.services.task_action_engine import prepare_action_proposals
from app.services.anticipation_engine import (
    list_anticipations,
    refresh_anticipations,
    set_anticipation_status,
)

router = APIRouter(prefix="/anticipation", tags=["Anticipation"])


class InsightResponse(BaseModel):
    id: str
    kind: str
    source_type: str
    source_id: str
    title: str
    explanation: str
    priority: Literal["low", "medium", "high"]
    due_at: datetime | None
    evidence: dict
    status: Literal["active", "dismissed", "resolved"]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, item: AnticipationInsight):
        try:
            evidence = json.loads(item.evidence_json or "{}")
        except (TypeError, json.JSONDecodeError):
            evidence = {}
        return cls(
            id=item.id, kind=item.kind, source_type=item.source_type,
            source_id=item.source_id, title=item.title,
            explanation=item.explanation, priority=item.priority,
            due_at=item.due_at, evidence=evidence, status=item.status,
            created_at=item.created_at, updated_at=item.updated_at,
        )


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "dismissed"]


@router.get("", response_model=list[InsightResponse])
async def get_anticipations(
    include_inactive: bool = False,
    user: User = Depends(get_current_memory_user),
    db: AsyncSession = Depends(get_db),
):
    items = await list_anticipations(db, user_id=user.id, include_inactive=include_inactive)
    return [InsightResponse.from_record(item) for item in items]


@router.post("/refresh", response_model=list[InsightResponse])
async def refresh_user_anticipations(
    user: User = Depends(get_current_memory_user),
    db: AsyncSession = Depends(get_db),
):
    items = await refresh_anticipations(db, user_id=user.id)
    await prepare_action_proposals(db, user_id=user.id)
    return [InsightResponse.from_record(item) for item in items]


@router.patch("/{insight_id}", response_model=InsightResponse)
async def update_anticipation(
    insight_id: str,
    request: StatusRequest,
    user: User = Depends(get_current_memory_user),
    db: AsyncSession = Depends(get_db),
):
    item = await set_anticipation_status(
        db, user_id=user.id, insight_id=insight_id, status=request.status
    )
    if item is None:
        raise HTTPException(status_code=404, detail={
            "code": "not_found", "message": "The anticipation insight was not found."
        })
    return InsightResponse.from_record(item)
