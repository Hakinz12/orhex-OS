"""Authenticated Walrus memory operations."""
import json
import logging
from datetime import timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.walrus.memwal import MemWalAdapter, WalrusAdapterError
from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.domain import MemorySyncRecord, User
from app.services.memory_persistence import (
    MemoryOwnerNotFoundError,
    MemoryPersistenceError,
    MemoryStateConflictError,
    MemoryWriteUncertainError,
    list_memories_for_user,
    get_owned_memory,
    namespace_for_user,
    persist_memory,
    recall_for_user,
    reconcile_memory,
    restore_memory,
    trash_memory,
)

logger = logging.getLogger("orhex.api.memory")
router = APIRouter(prefix="/memory", tags=["Walrus Memory"])
_walrus = MemWalAdapter()


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RememberRequest(StrictRequest):
    text: str = Field(..., min_length=3, max_length=8000)
    category: str = Field("personal", min_length=1, max_length=64)
    memory_type: str = Field("semantic", min_length=1, max_length=64)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[UUID] = None


class RecallRequest(StrictRequest):
    query: str = Field(..., min_length=3, max_length=1000)
    limit: int = Field(5, ge=1, le=20)


class ErrorResponse(BaseModel):
    error: str
    message: str
    operation_id: Optional[str] = None


class MemoryOperationResponse(BaseModel):
    status: str
    operation_id: str
    blob_id: Optional[str] = None
    memory_id: Optional[str] = None
    namespace: str
    category: str
    memory_type: str
    metadata: Dict[str, Any]
    created_at: str
    confirmed_at: Optional[str] = None
    trashed_at: Optional[str] = None
    permanent_delete_started_at: Optional[str] = None
    error: Optional[str] = None


class MemoryListItem(BaseModel):
    operation_id: str
    text: str
    status: str
    blob_id: Optional[str] = None
    category: str
    memory_type: str
    created_at: str
    confirmed_at: Optional[str] = None
    trashed_at: Optional[str] = None
    permanent_delete_started_at: Optional[str] = None


class MemoryListResponse(BaseModel):
    folder: str
    total: int
    memories: List[MemoryListItem]


class RecallHit(BaseModel):
    blob_id: str
    text: str
    distance: float
    score: Optional[float] = None
    created_at: Optional[str] = None
    namespace: str


class RecallResponse(BaseModel):
    query: str
    total: int
    results: List[RecallHit]
    namespace: str


def _record_response(record: MemorySyncRecord) -> MemoryOperationResponse:
    return MemoryOperationResponse(
        status=record.status,
        operation_id=record.id,
        blob_id=record.walrus_blob_id,
        memory_id=record.walrus_memory_id,
        namespace=record.namespace,
        category=record.category,
        memory_type=record.memory_type,
        metadata=json.loads(record.metadata_json or "{}"),
        created_at=_isoformat_timestamp(record.created_at),
        confirmed_at=_isoformat_timestamp(record.confirmed_at),
        trashed_at=_isoformat_timestamp(record.trashed_at),
        permanent_delete_started_at=_isoformat_timestamp(
            record.permanent_delete_started_at
        ),
        error=record.last_error,
    )


def _isoformat_timestamp(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _memory_list_item(record: MemorySyncRecord) -> MemoryListItem:
    return MemoryListItem(
        operation_id=record.id,
        text=record.text,
        status=record.status,
        blob_id=record.walrus_blob_id,
        category=record.category,
        memory_type=record.memory_type,
        created_at=_isoformat_timestamp(record.created_at),
        confirmed_at=_isoformat_timestamp(record.confirmed_at),
        trashed_at=_isoformat_timestamp(record.trashed_at),
        permanent_delete_started_at=_isoformat_timestamp(
            record.permanent_delete_started_at
        ),
    )


def _error(status_code: int, code: str, message: str, operation_id: Optional[str] = None):
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=code,
            message=message,
            operation_id=operation_id,
        ).model_dump(),
    )


@router.get("/status")
async def walrus_status(
    _user: User = Depends(get_current_user),
):
    """Authenticated connection-health and readiness report."""
    return await _walrus.check_readiness()


@router.post(
    "/remember",
    response_model=MemoryOperationResponse,
    responses={
        202: {"model": MemoryOperationResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": MemoryOperationResponse},
        503: {"model": ErrorResponse},
    },
)
async def remember_memory(
    req: RememberRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create an owned local operation before dispatching its Walrus write."""
    try:
        record = await persist_memory(
            db,
            _walrus,
            user_id=user.id,
            text=req.text,
            category=req.category,
            memory_type=req.memory_type,
            metadata=req.metadata,
            request_idempotency_key=(
                str(req.idempotency_key) if req.idempotency_key else None
            ),
        )
    except MemoryOwnerNotFoundError as exc:
        return _error(403, "invalid_owner", "Authenticated user is not provisioned.")
    except MemoryWriteUncertainError as exc:
        try:
            record = await get_owned_memory(
                db,
                user_id=user.id,
                record_id=exc.record_id or "",
            )
        except MemoryPersistenceError as db_exc:
            return _error(503, "database_error", str(db_exc), exc.record_id)
        if record is None:
            return _error(503, "database_error", str(exc), exc.record_id)
        response.status_code = 202
        return _record_response(record)
    except MemoryPersistenceError as exc:
        logger.error(
            "Memory persistence failed for operation %s: %s",
            exc.record_id or "not-created",
            type(exc).__name__,
        )
        return _error(
            503,
            "database_error",
            str(exc),
            exc.record_id,
        )
    except ValueError as exc:
        return _error(409, "idempotency_conflict", str(exc))

    if record.status == "confirmed":
        response.status_code = 201
    elif record.status == "failed":
        response.status_code = 502
    else:
        response.status_code = 202
    return _record_response(record)


@router.post(
    "/recall",
    response_model=RecallResponse,
    responses={401: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def recall_memories(
    req: RecallRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Perform semantic recall only in the authenticated user's namespace."""
    try:
        hits = await recall_for_user(
            db,
            _walrus,
            user_id=user.id,
            query=req.query,
            limit=req.limit,
        )
    except MemoryOwnerNotFoundError:
        return _error(403, "invalid_owner", "Authenticated user is not provisioned.")
    except MemoryPersistenceError as exc:
        logger.error("Memory owner validation failed: %s", type(exc).__name__)
        return _error(503, "database_error", str(exc))
    except WalrusAdapterError as exc:
        return _error(502, "walrus_recall_failed", str(exc))

    return RecallResponse(
        query=req.query,
        total=len(hits),
        results=[RecallHit(**hit) for hit in hits],
        namespace=namespace_for_user(user.id),
    )


@router.get(
    "/records/{folder}",
    response_model=MemoryListResponse,
    responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def list_memory_records(
    folder: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List only this user's confirmed active memories or Trash records."""
    if folder not in {"active", "trash"}:
        return _error(404, "not_found", "The requested memory folder was not found.")
    try:
        records = await list_memories_for_user(db, user_id=user.id, folder=folder)
    except MemoryOwnerNotFoundError:
        return _error(403, "invalid_owner", "Authenticated user is not provisioned.")
    except MemoryPersistenceError as exc:
        logger.error("Memory list failed: %s", type(exc).__name__)
        return _error(503, "database_error", str(exc))
    return MemoryListResponse(
        folder=folder,
        total=len(records),
        memories=[_memory_list_item(record) for record in records],
    )


@router.get(
    "/operations/{record_id}",
    response_model=MemoryOperationResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def memory_operation_status(
    record_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Read an operation only if it belongs to the authenticated user."""
    try:
        record = await get_owned_memory(db, user_id=user.id, record_id=record_id)
    except MemoryPersistenceError as exc:
        logger.error("Memory status lookup failed: %s", type(exc).__name__)
        return _error(503, "database_error", str(exc))
    if record is None:
        return _error(404, "not_found", "Memory operation was not found.")
    return _record_response(record)


@router.post(
    "/operations/{record_id}/reconcile",
    response_model=MemoryOperationResponse,
    responses={
        202: {"model": MemoryOperationResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": MemoryOperationResponse},
        503: {"model": ErrorResponse},
    },
)
async def reconcile_operation(
    record_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reconcile through SDK job-status lookup; never resubmit a write."""
    try:
        record = await reconcile_memory(
            db,
            _walrus,
            user_id=user.id,
            record_id=record_id,
        )
    except MemoryOwnerNotFoundError:
        return _error(404, "not_found", "Memory operation was not found.")
    except MemoryWriteUncertainError as exc:
        record = await get_owned_memory(db, user_id=user.id, record_id=record_id)
        if record is None:
            return _error(404, "not_found", "Memory operation was not found.")
        logger.warning("Memory reconciliation remains uncertain: %s", type(exc).__name__)
    except MemoryPersistenceError as exc:
        logger.error("Memory reconciliation database failure: %s", type(exc).__name__)
        return _error(503, "database_error", str(exc), exc.record_id)

    if record.status == "confirmed":
        response.status_code = 200
    elif record.status == "failed":
        response.status_code = 502
    else:
        response.status_code = 202
    return _record_response(record)


@router.delete(
    "/operations/{record_id}",
    response_model=MemoryOperationResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def delete_memory_operation(
    record_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        record = await trash_memory(db, user_id=user.id, record_id=record_id)
    except MemoryPersistenceError as exc:
        if isinstance(exc, MemoryStateConflictError):
            return _error(409, "memory_state_conflict", str(exc), exc.record_id)
        logger.error("Memory trash operation failed: %s", type(exc).__name__)
        return _error(503, "database_error", str(exc), exc.record_id)
    if record is None:
        return _error(404, "not_found", "Memory operation was not found.")
    return _record_response(record)


@router.post(
    "/operations/{record_id}/restore",
    response_model=MemoryOperationResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def restore_memory_operation(
    record_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Restore a trashed memory only for its authenticated owner."""
    try:
        record = await restore_memory(db, user_id=user.id, record_id=record_id)
    except MemoryPersistenceError as exc:
        if isinstance(exc, MemoryStateConflictError):
            return _error(409, "memory_state_conflict", str(exc), exc.record_id)
        logger.error("Memory restore failed: %s", type(exc).__name__)
        return _error(503, "database_error", str(exc), exc.record_id)
    if record is None:
        return _error(404, "not_found", "Memory operation was not found.")
    return _record_response(record)


@router.post(
    "/operations/{record_id}/permanent-delete",
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        501: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def permanently_delete_memory_operation(
    record_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Check permanent-delete support without deleting remote data in Phase 1."""
    try:
        record = await get_owned_memory(db, user_id=user.id, record_id=record_id)
    except MemoryPersistenceError as exc:
        logger.error("Permanent-delete ownership lookup failed: %s", type(exc).__name__)
        return _error(503, "database_error", str(exc), exc.record_id)
    if record is None:
        return _error(404, "not_found", "Memory operation was not found.")
    if record.trashed_at is None:
        return _error(
            409,
            "memory_not_trashed",
            "Only memories in Trash can be permanently deleted.",
            record.id,
        )
    if record.permanent_delete_started_at is not None:
        return _error(
            409,
            "permanent_delete_in_progress",
            "Permanent deletion has already started; do not retry or restore this memory.",
            record.id,
        )
    if not record.walrus_blob_id:
        return _error(
            409,
            "memory_not_eligible",
            "The memory has no confirmed Walrus blob ID and cannot be permanently deleted.",
            record.id,
        )
    try:
        result = await _walrus.delete_memory(
            namespace=record.namespace,
            memory_id=record.walrus_blob_id,
        )
    except Exception as exc:
        logger.error(
            "Walrus permanent-delete capability check failed: %s",
            type(exc).__name__,
        )
        return _error(
            503,
            "capability_check_failed",
            "Walrus permanent-delete support could not be verified.",
            record.id,
        )
    if result.get("status") == "unsupported" or result.get("supported") is False:
        return _error(
            501,
            "unsupported_operation",
            "Walrus permanent deletion is unsupported. This memory remains in Trash and its remote data is unchanged.",
            record.id,
        )
    logger.error("Walrus adapter returned an unexpected permanent-delete capability.")
    return _error(
        501,
        "unsupported_operation",
        "Permanent deletion is not implemented in this phase; no deletion was performed.",
        record.id,
    )
