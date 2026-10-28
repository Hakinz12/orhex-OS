"""Durable local ownership and reconciliation for Walrus memory writes."""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.walrus.base import BaseWalrusMemory
from app.core.config import settings
from app.models.domain import MemorySyncRecord, User, generate_uuid, utc_now

logger = logging.getLogger("orhrex.memory.persistence")

PERSISTENCE_STATES = frozenset(
    {"pending", "confirmed", "failed", "reconciliation_required"}
)
_DEFINITE_FAILURE_STATUSES = frozenset({"failed", "rate_limited"})


class MemoryPersistenceError(RuntimeError):
    """A local database operation prevented a reliable persistence result."""

    def __init__(self, message: str, record_id: Optional[str] = None):
        super().__init__(message)
        self.record_id = record_id


class MemoryOwnerNotFoundError(MemoryPersistenceError):
    """The requested application user does not exist."""


class MemoryWriteUncertainError(MemoryPersistenceError):
    """Walrus may have accepted a write, so it must not be blindly retried."""


def namespace_for_user(user_id: str, base_namespace: Optional[str] = None) -> str:
    """Derive a stable per-user namespace without accepting caller-supplied namespaces."""
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("A non-empty application user ID is required.")
    prefix = base_namespace or settings.MEMWAL_NAMESPACE
    if not prefix or not prefix.strip():
        raise ValueError("MEMWAL_NAMESPACE must be non-empty.")
    user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:20]
    return f"{prefix[:107]}-{user_hash}"


async def persist_memory(
    session: AsyncSession,
    walrus: BaseWalrusMemory,
    *,
    user_id: str,
    text: str,
    category: str = "personal",
    memory_type: str = "semantic",
    metadata: Optional[Dict[str, Any]] = None,
    request_idempotency_key: Optional[str] = None,
    source: str = "user_confirmed",
    confidence: float = 1.0,
) -> MemorySyncRecord:
    """Commit a local pending operation before sending one idempotent Walrus write."""
    try:
        user = await session.get(User, user_id)
        if user is None:
            await session.rollback()
            raise MemoryOwnerNotFoundError("Application user does not exist.")
        serialized_metadata = json.dumps(
            metadata or {}, sort_keys=True, separators=(",", ":")
        )
        if request_idempotency_key is not None and (
            not request_idempotency_key.strip()
            or len(request_idempotency_key) > 256
        ):
            raise ValueError("Request idempotency key must contain 1-256 characters.")
    except MemoryOwnerNotFoundError:
        raise
    except (SQLAlchemyError, TypeError, ValueError) as exc:
        await session.rollback()
        raise MemoryPersistenceError(
            "Could not validate the owner or memory metadata before writing."
        ) from exc

    namespace = namespace_for_user(user_id)
    if request_idempotency_key is None:
        idempotency_key = generate_uuid()
    else:
        idempotency_key = hashlib.sha256(
            f"{user_id}\0{request_idempotency_key}".encode("utf-8")
        ).hexdigest()
        try:
            existing_result = await session.execute(
                select(MemorySyncRecord).where(
                    MemorySyncRecord.user_id == user_id,
                    MemorySyncRecord.idempotency_key == idempotency_key,
                )
            )
            existing = existing_result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            await session.rollback()
            raise MemoryPersistenceError(
                "Could not check the existing memory operation."
            ) from exc
        if existing is not None:
            same_operation = (
                existing.namespace == namespace
                and existing.text == text
                and existing.category == category
                and existing.memory_type == memory_type
                and existing.metadata_json == serialized_metadata
                and existing.source == source
                and existing.confidence == confidence
            )
            if not same_operation:
                await session.rollback()
                raise ValueError(
                    "Request idempotency key was already used for different memory data."
                )
            try:
                await session.commit()
            except SQLAlchemyError as exc:
                await session.rollback()
                raise MemoryPersistenceError(
                    "Could not close the existing operation read transaction.",
                    existing.id,
                ) from exc
            return existing

    operation_id = generate_uuid()
    record = MemorySyncRecord(
        id=operation_id,
        user_id=user_id,
        namespace=namespace,
        idempotency_key=idempotency_key,
        text=text,
        category=category,
        memory_type=memory_type,
        metadata_json=serialized_metadata,
        status="pending",
        source=source,
        confidence=confidence,
        created_at=utc_now(),
        attempted_at=utc_now(),
    )
    session.add(record)
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise MemoryPersistenceError(
            "Could not durably record the memory operation; Walrus was not called.",
            operation_id,
        ) from exc

    try:
        result = await walrus.remember(
            namespace=namespace,
            text=text,
            category=category,
            memory_type=memory_type,
            metadata=metadata,
            idempotency_key=record.idempotency_key,
        )
    except Exception as exc:
        record.status = "reconciliation_required"
        record.last_error = type(exc).__name__
        record.walrus_job_id = getattr(exc, "job_id", None)
        record.updated_at = utc_now()
        await _commit_state(session, record, "Walrus write outcome is uncertain.")
        raise MemoryWriteUncertainError(
            "Walrus write outcome is uncertain; reconcile before retrying.", record.id
        ) from exc

    result_status = result.get("status")
    record.walrus_job_id = result.get("job_id")
    record.last_error = result.get("error")
    record.updated_at = utc_now()

    reported_namespace = result.get("namespace")
    if (
        result_status == "confirmed"
        and result.get("blob_id")
        and reported_namespace in (None, namespace)
    ):
        record.status = "confirmed"
        record.walrus_blob_id = result["blob_id"]
        record.walrus_memory_id = result.get("memory_id")
        record.confirmed_at = utc_now()
    elif result_status in _DEFINITE_FAILURE_STATUSES or (
        result_status == "failed"
        and (
            result.get("definite_failure") is True
            or result.get("error_type") == "MemWalRememberJobFailed"
        )
    ):
        record.status = "failed"
    else:
        record.status = "reconciliation_required"
        if result_status == "confirmed":
            record.last_error = "Walrus confirmation did not match the owned operation."

    await _commit_state(
        session,
        record,
        "Could not save the Walrus write result locally.",
        outcome_uncertain=record.status == "confirmed",
    )
    return record


async def _commit_state(
    session: AsyncSession,
    record: MemorySyncRecord,
    message: str,
    *,
    outcome_uncertain: bool = False,
) -> None:
    record_id = record.id
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        if outcome_uncertain:
            logger.error(
                "Walrus confirmed operation %s but local confirmation failed.",
                record_id,
            )
            raise MemoryWriteUncertainError(
                "Walrus confirmed the write, but local confirmation failed; "
                "the durable pending record must be reconciled.",
                record_id,
            ) from exc
        raise MemoryPersistenceError(message, record_id) from exc


async def recover_pending_operations(
    session: AsyncSession,
    *,
    older_than: timedelta = timedelta(minutes=5),
) -> int:
    """Move stale pending writes to reconciliation without resubmitting them."""
    cutoff = datetime.now(timezone.utc) - older_than
    try:
        result = await session.execute(
            select(MemorySyncRecord).where(
                MemorySyncRecord.status == "pending",
                MemorySyncRecord.updated_at <= cutoff,
            )
        )
        records = list(result.scalars())
        for record in records:
            record.status = "reconciliation_required"
            record.last_error = "Process ended before the Walrus outcome was recorded."
            record.updated_at = utc_now()
        await session.commit()
        return len(records)
    except SQLAlchemyError as exc:
        await session.rollback()
        raise MemoryPersistenceError(
            "Could not recover stale memory operations."
        ) from exc


async def reconcile_memory(
    session: AsyncSession,
    walrus: BaseWalrusMemory,
    *,
    user_id: str,
    record_id: str,
) -> MemorySyncRecord:
    """Resolve an uncertain operation from its SDK job status; never resubmit it."""
    try:
        result = await session.execute(
            select(MemorySyncRecord).where(
                MemorySyncRecord.id == record_id,
                MemorySyncRecord.user_id == user_id,
            )
        )
        record = result.scalar_one_or_none()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise MemoryPersistenceError("Could not load the owned memory operation.") from exc

    if record is None:
        raise MemoryOwnerNotFoundError("Memory operation does not belong to this user.")
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise MemoryPersistenceError(
            "Could not close the local read transaction before reconciliation.",
            record.id,
        ) from exc
    if record.status not in {"pending", "reconciliation_required"}:
        return record
    if not record.walrus_job_id:
        record.status = "reconciliation_required"
        record.last_error = (
            "No Walrus job ID is available; manual verification is required."
        )
        record.updated_at = utc_now()
        await _commit_state(session, record, "Could not save reconciliation state.")
        return record

    try:
        status = await walrus.get_remember_status(record.walrus_job_id)
    except Exception as exc:
        record.status = "reconciliation_required"
        record.last_error = type(exc).__name__
        record.updated_at = utc_now()
        await _commit_state(session, record, "Could not save reconciliation state.")
        raise MemoryWriteUncertainError(
            "Walrus job status could not be verified.", record.id
        ) from exc

    if status.get("status") == "done" and status.get("blob_id"):
        if status.get("namespace") not in (None, record.namespace):
            record.last_error = "Walrus job namespace did not match the owned record."
            record.status = "reconciliation_required"
        else:
            record.walrus_blob_id = status["blob_id"]
            record.walrus_memory_id = status.get("memory_id")
            record.status = "confirmed"
            record.confirmed_at = utc_now()
            record.last_error = None
    elif status.get("status") == "failed":
        record.status = "failed"
        record.last_error = status.get("error") or "Walrus job failed."
    else:
        record.status = "reconciliation_required"
        record.last_error = status.get("error") or (
            "Walrus job is not in a confirmed terminal state."
        )
    record.updated_at = utc_now()
    await _commit_state(
        session,
        record,
        "Could not save the reconciled Walrus job state.",
        outcome_uncertain=record.status == "confirmed",
    )
    return record


async def get_owned_memory(
    session: AsyncSession, *, user_id: str, record_id: str
) -> Optional[MemorySyncRecord]:
    """Load a memory only when both its record and owner IDs match."""
    try:
        result = await session.execute(
            select(MemorySyncRecord).where(
                MemorySyncRecord.id == record_id,
                MemorySyncRecord.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise MemoryPersistenceError("Could not load the owned memory record.") from exc


async def recall_for_user(
    session: AsyncSession,
    walrus: BaseWalrusMemory,
    *,
    user_id: str,
    query: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Recall only from the namespace derived for the application user."""
    try:
        user = await session.get(User, user_id)
        if user is None:
            await session.rollback()
            raise MemoryOwnerNotFoundError("Application user does not exist.")
        await session.commit()
    except MemoryOwnerNotFoundError:
        raise
    except SQLAlchemyError as exc:
        await session.rollback()
        raise MemoryPersistenceError(
            "Could not validate the owner before recalling memories."
        ) from exc
    return await walrus.recall(
        namespace=namespace_for_user(user_id),
        query=query,
        limit=limit,
    )
