"""Owner-scoped task management and approval-gated task actions."""
import json
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.dependencies import get_current_memory_user
from app.core.database import get_db
from app.models.domain import ActionProposal, Task, User
from app.services.task_action_engine import prepare_action_proposals

router = APIRouter(prefix="/tasks", tags=["Tasks & Actions"])

def _task(item: Task):
    return TaskResponse(id=item.id, title=item.title, description=item.description, priority=item.priority, status=item.status, due_at=item.due_at, source_type=item.source_type, source_id=item.source_id, created_at=item.created_at, updated_at=item.updated_at, completed_at=item.completed_at)

class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4000)
    priority: Literal["low", "medium", "high"] = "medium"
    due_at: datetime | None = None

class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4000)
    priority: Literal["low", "medium", "high"] | None = None
    status: Literal["todo", "in_progress", "completed", "cancelled"] | None = None
    due_at: datetime | None = None

class TaskResponse(BaseModel):
    id: str; title: str; description: str | None; priority: str; status: str
    due_at: datetime | None; source_type: str | None; source_id: str | None
    created_at: datetime; updated_at: datetime; completed_at: datetime | None

class ProposalResponse(BaseModel):
    id: str; action_type: str; title: str; rationale: str; payload: dict; status: str
    created_at: datetime; updated_at: datetime; executed_at: datetime | None

class ProposalStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["approved", "dismissed"]

@router.get("", response_model=list[TaskResponse])
async def list_tasks(status: Literal["todo", "in_progress", "completed", "cancelled"] | None = None, user: User = Depends(get_current_memory_user), db: AsyncSession = Depends(get_db)):
    q=select(Task).where(Task.user_id==user.id)
    if status: q=q.where(Task.status==status)
    rows=(await db.scalars(q.order_by(Task.due_at.asc().nullslast(), Task.created_at.desc()))).all()
    return [_task(x) for x in rows]

@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(body: TaskCreate, user: User = Depends(get_current_memory_user), db: AsyncSession = Depends(get_db)):
    item=Task(user_id=user.id,title=body.title.strip(),description=body.description,priority=body.priority,due_at=body.due_at)
    if not item.title: raise HTTPException(422, "Task title cannot be blank.")
    db.add(item); await db.commit(); await db.refresh(item); return _task(item)

@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(task_id: str, body: TaskUpdate, user: User = Depends(get_current_memory_user), db: AsyncSession = Depends(get_db)):
    item=await db.scalar(select(Task).where(Task.id==task_id,Task.user_id==user.id))
    if item is None: raise HTTPException(404, "Task not found.")
    for key,value in body.model_dump(exclude_unset=True).items():
        if key=="title": value=value.strip();
        if key=="title" and not value: raise HTTPException(422,"Task title cannot be blank.")
        setattr(item,key,value)
    if item.status=="completed" and item.completed_at is None: item.completed_at=datetime.now(timezone.utc)
    elif item.status!="completed": item.completed_at=None
    item.updated_at=datetime.now(timezone.utc); await db.commit(); await db.refresh(item); return _task(item)

@router.get("/actions", response_model=list[ProposalResponse])
async def list_actions(user: User = Depends(get_current_memory_user), db: AsyncSession = Depends(get_db)):
    rows=(await db.scalars(select(ActionProposal).where(ActionProposal.user_id==user.id).order_by(ActionProposal.created_at.desc()))).all()
    return [ProposalResponse(id=x.id,action_type=x.action_type,title=x.title,rationale=x.rationale,payload=json.loads(x.payload_json or "{}"),status=x.status,created_at=x.created_at,updated_at=x.updated_at,executed_at=x.executed_at) for x in rows]

@router.post("/actions/prepare", response_model=list[ProposalResponse])
async def prepare_actions(user: User = Depends(get_current_memory_user), db: AsyncSession = Depends(get_db)):
    rows = await prepare_action_proposals(db, user_id=user.id)
    return [ProposalResponse(
        id=x.id, action_type=x.action_type, title=x.title, rationale=x.rationale,
        payload=json.loads(x.payload_json or "{}"), status=x.status,
        created_at=x.created_at, updated_at=x.updated_at, executed_at=x.executed_at,
    ) for x in rows]

@router.post("/actions/{action_id}/approve", response_model=ProposalResponse)
async def approve_action(action_id: str, body: ProposalStatus, user: User = Depends(get_current_memory_user), db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    if body.status=="approved":
        item = await db.scalar(select(ActionProposal).where(
            ActionProposal.id == action_id,
            ActionProposal.user_id == user.id,
        ))
        if item is None:
            raise HTTPException(404, "Action proposal not found.")
        if item.action_type != "create_task":
            raise HTTPException(400, "Unsupported action type.")
        try:
            payload = json.loads(item.payload_json or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Proposal payload must be an object.")
            title = payload.get("title")
            description = payload.get("description")
            priority = payload.get("priority", "medium")
            due_at = payload.get("due_at")
            source_type = payload.get("source_type")
            source_id = payload.get("source_id")
            if not isinstance(title, str) or not title.strip() or len(title) > 256:
                raise ValueError("Proposal task title is invalid.")
            if description is not None and (
                not isinstance(description, str) or len(description) > 4000
            ):
                raise ValueError("Proposal task description is invalid.")
            if priority not in {"low", "medium", "high"}:
                raise ValueError("Proposal task priority is invalid.")
            if source_type is not None and (
                not isinstance(source_type, str) or len(source_type) > 32
            ):
                raise ValueError("Proposal source type is invalid.")
            if source_id is not None and (
                not isinstance(source_id, str) or len(source_id) > 64
            ):
                raise ValueError("Proposal source id is invalid.")
            due = datetime.fromisoformat(due_at) if due_at is not None else None
            task = Task(
                user_id=user.id,
                title=title.strip(),
                description=description,
                priority=priority,
                due_at=due,
                source_type=source_type,
                source_id=source_id,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(409, "The action proposal payload is invalid.") from exc

        transition = await db.execute(
            update(ActionProposal)
            .where(
                ActionProposal.id == action_id,
                ActionProposal.user_id == user.id,
                ActionProposal.status == "proposed",
                ActionProposal.action_type == "create_task",
            )
            .values(status="executed", executed_at=now, updated_at=now)
            .returning(ActionProposal.id)
            .execution_options(synchronize_session=False)
        )
        if transition.scalar_one_or_none() is None:
            await db.rollback()
            raise HTTPException(409, "This action is no longer awaiting approval.")
        db.add(task)
    else:
        transition = await db.execute(
            update(ActionProposal)
            .where(
                ActionProposal.id == action_id,
                ActionProposal.user_id == user.id,
                ActionProposal.status == "proposed",
            )
            .values(status="dismissed", updated_at=now)
            .returning(ActionProposal.id)
            .execution_options(synchronize_session=False)
        )
        if transition.scalar_one_or_none() is None:
            await db.rollback()
            item = await db.scalar(select(ActionProposal).where(
                ActionProposal.id == action_id,
                ActionProposal.user_id == user.id,
            ))
            if item is None:
                raise HTTPException(404, "Action proposal not found.")
            raise HTTPException(409, "This action is no longer awaiting approval.")

    await db.commit()
    item = await db.scalar(select(ActionProposal).where(
        ActionProposal.id == action_id,
        ActionProposal.user_id == user.id,
    ))
    if item is None:
        raise HTTPException(404, "Action proposal not found.")
    await db.refresh(item)
    return ProposalResponse(id=item.id,action_type=item.action_type,title=item.title,rationale=item.rationale,payload=json.loads(item.payload_json or "{}"),status=item.status,created_at=item.created_at,updated_at=item.updated_at,executed_at=item.executed_at)
