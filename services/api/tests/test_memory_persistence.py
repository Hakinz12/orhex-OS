"""Database-backed tests for durable Walrus memory operations."""
import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import (
    Base,
    _enable_sqlite_foreign_keys,
    _migrate_memory_sync_records,
    _migrate_user_memory_trash_retention,
)
from app.models.domain import MemorySyncRecord, User, utc_now
from app.services.memory_persistence import (
    MemoryOwnerNotFoundError,
    MemoryPersistenceError,
    MemoryWriteUncertainError,
    active_memory_context_for_user,
    get_owned_memory,
    namespace_for_user,
    persist_memory,
    recall_for_user,
    reconcile_memory,
    recover_pending_operations,
    restore_memory,
    trash_memory,
)


class FakeWalrus:
    def __init__(self, result=None, write_error=None, job_status=None, on_remember=None):
        self.result = result or {
            "status": "confirmed",
            "blob_id": "blob-confirmed",
            "memory_id": "mem-confirmed",
        }
        self.write_error = write_error
        self.job_status = job_status or {"status": "pending"}
        self.on_remember = on_remember
        self.remember = AsyncMock(side_effect=self._remember)
        self.recall = AsyncMock(return_value=[])
        self.get_remember_status = AsyncMock(return_value=self.job_status)

    async def _remember(self, **kwargs):
        if self.on_remember is not None:
            await self.on_remember(**kwargs)
        if self.write_error is not None:
            raise self.write_error
        return self.result


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def add_user(session, user_id="owner-1"):
    user = User(id=user_id, preferred_name="Test Owner")
    session.add(user)
    await session.commit()
    return user


@pytest.mark.asyncio
async def test_successful_write_is_persisted_with_user_metadata_and_key(db_session):
    await add_user(db_session)

    async def assert_pending_was_committed(**kwargs):
        pending = await db_session.execute(
            select(MemorySyncRecord).where(
                MemorySyncRecord.idempotency_key == kwargs["idempotency_key"]
            )
        )
        assert pending.scalar_one().status == "pending"

    walrus = FakeWalrus(on_remember=assert_pending_was_committed)

    record = await persist_memory(
        db_session,
        walrus,
        user_id="owner-1",
        text="The user prefers concise updates.",
        category="personal",
        memory_type="preference",
        metadata={"origin": "chat", "verified": True},
    )

    assert record.status == "confirmed"
    assert record.walrus_blob_id == "blob-confirmed"
    assert record.confirmed_at is not None
    assert record.user_id == "owner-1"
    assert record.namespace == namespace_for_user("owner-1")
    assert json.loads(record.metadata_json) == {
        "origin": "chat",
        "verified": True,
    }
    walrus.remember.assert_awaited_once()
    assert walrus.remember.await_args.kwargs["idempotency_key"] == record.idempotency_key
    assert walrus.remember.await_args.kwargs["namespace"] == record.namespace


@pytest.mark.asyncio
async def test_repeated_request_key_returns_existing_operation_without_retry(db_session):
    await add_user(db_session)
    walrus = FakeWalrus()
    args = {
        "user_id": "owner-1",
        "text": "Same user action retried by the client.",
        "request_idempotency_key": "client-request-123",
    }

    first = await persist_memory(db_session, walrus, **args)
    second = await persist_memory(db_session, walrus, **args)

    assert second.id == first.id
    assert second.idempotency_key == first.idempotency_key
    assert walrus.remember.await_count == 1


@pytest.mark.asyncio
async def test_request_key_cannot_be_reused_for_different_memory_data(db_session):
    await add_user(db_session)
    walrus = FakeWalrus()
    await persist_memory(
        db_session,
        walrus,
        user_id="owner-1",
        text="First request payload.",
        request_idempotency_key="client-request-123",
    )

    with pytest.raises(ValueError, match="different memory data"):
        await persist_memory(
            db_session,
            walrus,
            user_id="owner-1",
            text="Changed request payload.",
            request_idempotency_key="client-request-123",
        )
    assert walrus.remember.await_count == 1


@pytest.mark.asyncio
async def test_write_confirmation_from_wrong_namespace_is_not_accepted(db_session):
    await add_user(db_session)
    walrus = FakeWalrus(
        result={
            "status": "confirmed",
            "blob_id": "blob-wrong-namespace",
            "namespace": "another-users-namespace",
        }
    )

    record = await persist_memory(
        db_session, walrus, user_id="owner-1", text="Wrong namespace response."
    )

    assert record.status == "reconciliation_required"
    assert record.walrus_blob_id is None


@pytest.mark.asyncio
async def test_definite_walrus_write_failure_is_recorded(db_session):
    await add_user(db_session)
    walrus = FakeWalrus(
        result={
            "status": "failed",
            "error": "Walrus job failed.",
            "error_type": "MemWalRememberJobFailed",
            "job_id": "job-failed",
        }
    )

    record = await persist_memory(
        db_session, walrus, user_id="owner-1", text="A failed memory write."
    )

    assert record.status == "failed"
    assert record.walrus_job_id == "job-failed"
    assert record.last_error == "Walrus job failed."


@pytest.mark.asyncio
async def test_database_failure_before_write_never_calls_walrus(db_session, monkeypatch):
    await add_user(db_session)
    walrus = FakeWalrus()
    original_commit = db_session.commit

    async def fail_commit():
        raise SQLAlchemyError("injected local failure")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    with pytest.raises(MemoryPersistenceError, match="Walrus was not called"):
        await persist_memory(
            db_session, walrus, user_id="owner-1", text="No external write."
        )
    walrus.remember.assert_not_awaited()
    monkeypatch.setattr(db_session, "commit", original_commit)


@pytest.mark.asyncio
async def test_database_failure_after_walrus_success_leaves_pending_for_recovery(
    db_session, monkeypatch
):
    await add_user(db_session)
    walrus = FakeWalrus()
    original_commit = db_session.commit
    commit_count = 0

    async def fail_confirmation_commit():
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise SQLAlchemyError("injected confirmation failure")
        await original_commit()

    monkeypatch.setattr(db_session, "commit", fail_confirmation_commit)
    with pytest.raises(MemoryWriteUncertainError) as error:
        await persist_memory(
            db_session, walrus, user_id="owner-1", text="Walrus accepted this."
        )
    assert walrus.remember.await_count == 1

    monkeypatch.setattr(db_session, "commit", original_commit)
    record = await db_session.get(MemorySyncRecord, error.value.record_id)
    assert record is not None
    assert record.status == "pending"

    assert await recover_pending_operations(
        db_session, older_than=timedelta(seconds=0)
    ) == 1
    assert record.status == "reconciliation_required"


@pytest.mark.asyncio
async def test_ambiguous_write_failure_is_not_retried(db_session):
    await add_user(db_session)
    walrus = FakeWalrus(write_error=ConnectionError("connection dropped"))

    with pytest.raises(MemoryWriteUncertainError) as error:
        await persist_memory(
            db_session, walrus, user_id="owner-1", text="Outcome may be unknown."
        )

    record = await db_session.get(MemorySyncRecord, error.value.record_id)
    assert record.status == "reconciliation_required"
    assert walrus.remember.await_count == 1
    result = await reconcile_memory(
        db_session, walrus, user_id="owner-1", record_id=record.id
    )
    assert result.status == "reconciliation_required"
    walrus.remember.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_pending_recovery_does_not_resubmit(db_session):
    await add_user(db_session)
    record = MemorySyncRecord(
        id="pending-op",
        user_id="owner-1",
        idempotency_key="pending-op",
        namespace=namespace_for_user("owner-1"),
        text="Process stopped before recording a result.",
        status="pending",
        created_at=utc_now() - timedelta(minutes=10),
        updated_at=utc_now() - timedelta(minutes=10),
    )
    db_session.add(record)
    await db_session.commit()
    walrus = FakeWalrus()

    recovered = await recover_pending_operations(
        db_session, older_than=timedelta(minutes=5)
    )

    assert recovered == 1
    assert record.status == "reconciliation_required"
    walrus.remember.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_status_reconciliation_confirms_without_a_second_write(db_session):
    await add_user(db_session)
    record = MemorySyncRecord(
        id="uncertain-op",
        user_id="owner-1",
        idempotency_key="uncertain-op",
        namespace=namespace_for_user("owner-1"),
        text="Walrus write was accepted.",
        status="reconciliation_required",
        walrus_job_id="job-123",
    )
    db_session.add(record)
    await db_session.commit()
    walrus = FakeWalrus(
        job_status={
            "status": "done",
            "blob_id": "blob-reconciled",
            "namespace": record.namespace,
        }
    )

    reconciled = await reconcile_memory(
        db_session, walrus, user_id="owner-1", record_id=record.id
    )

    assert reconciled.status == "confirmed"
    assert reconciled.walrus_blob_id == "blob-reconciled"
    walrus.get_remember_status.assert_awaited_once_with("job-123")
    walrus.remember.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_and_namespace_isolation(db_session):
    await add_user(db_session, "owner-1")
    await add_user(db_session, "owner-2")
    walrus = FakeWalrus()
    record = await persist_memory(
        db_session, walrus, user_id="owner-1", text="Private to owner one."
    )
    assert namespace_for_user("owner-1") != namespace_for_user("owner-2")

    assert await get_owned_memory(
        db_session, user_id="owner-1", record_id=record.id
    ) == record
    assert await get_owned_memory(
        db_session, user_id="owner-2", record_id=record.id
    ) is None

    await recall_for_user(db_session, walrus, user_id="owner-2", query="private")
    assert walrus.recall.await_args.kwargs["namespace"] == namespace_for_user("owner-2")
    assert walrus.recall.await_args.kwargs["namespace"] != record.namespace


@pytest.mark.asyncio
async def test_active_memory_context_excludes_trashed_memories(db_session):
    await add_user(db_session, "owner-1")
    walrus = FakeWalrus()
    trashed = await persist_memory(
        db_session, walrus, user_id="owner-1", text="Must not enter AI context."
    )
    await persist_memory(
        db_session, walrus, user_id="owner-1", text="This remains active context."
    )
    trashed.trashed_at = utc_now()
    await db_session.commit()

    context = await active_memory_context_for_user(db_session, user_id="owner-1")

    assert context == ["This remains active context."]


@pytest.mark.asyncio
async def test_active_memory_context_excludes_permanent_delete_started_memories(db_session):
    await add_user(db_session, "owner-1")
    walrus = FakeWalrus()
    deleting = await persist_memory(
        db_session, walrus, user_id="owner-1", text="Must not enter AI context."
    )
    await persist_memory(
        db_session, walrus, user_id="owner-1", text="This remains active context."
    )
    deleting.permanent_delete_started_at = utc_now()
    await db_session.commit()

    context = await active_memory_context_for_user(db_session, user_id="owner-1")

    assert context == ["This remains active context."]


@pytest.mark.asyncio
async def test_concurrent_trash_and_restore_transitions_are_atomic(tmp_path):
    database_path = tmp_path / "memory-lifecycle-concurrency.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        connect_args={"timeout": 10},
    )
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    walrus = FakeWalrus()
    try:
        async with factory() as session:
            await add_user(session, "owner-1")
            record = await persist_memory(
                session, walrus, user_id="owner-1", text="Concurrent lifecycle test."
            )
            record_id = record.id
            await trash_memory(session, user_id="owner-1", record_id=record_id)

        start = asyncio.Event()

        async def transition(action):
            async with factory() as session:
                await start.wait()
                record = await action(
                    session, user_id="owner-1", record_id=record_id
                )
                return action.__name__, record.trashed_at is not None

        trash_task = asyncio.create_task(transition(trash_memory))
        restore_task = asyncio.create_task(transition(restore_memory))
        start.set()
        results = await asyncio.gather(
            trash_task, restore_task, return_exceptions=True
        )

        assert all(not isinstance(result, Exception) for result in results)
        states = dict(results)
        assert states == {"trash_memory": True, "restore_memory": False}
        async with factory() as session:
            final = await session.get(MemorySyncRecord, record_id)
            assert final is not None
            assert final.user_id == "owner-1"
            assert final.permanent_delete_started_at is None
            assert (final.trashed_at is not None) in states.values()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_user_is_rejected_before_walrus_write(db_session):
    walrus = FakeWalrus()

    with pytest.raises(MemoryOwnerNotFoundError):
        await persist_memory(
            db_session, walrus, user_id="missing", text="No owner exists."
        )

    walrus.remember.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_state_constraint_rejects_unknown_status(db_session):
    await add_user(db_session)
    db_session.add(
        MemorySyncRecord(
            id="invalid-status",
            user_id="owner-1",
            idempotency_key="invalid-status",
            namespace=namespace_for_user("owner-1"),
            text="Invalid state.",
            status="pretend_confirmed",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_memory_owner_foreign_key_is_enforced_by_sqlite(db_session):
    db_session.add(
        MemorySyncRecord(
            id="orphaned-memory",
            user_id="missing-owner",
            idempotency_key="orphaned-memory",
            namespace="orhex-isolated",
            text="This record must not persist without a user.",
            status="pending",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_existing_database_schema_is_upgraded_in_place():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE memory_sync_records ("
                "id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64) NOT NULL, "
                "walrus_blob_id VARCHAR(256), text TEXT NOT NULL, "
                "category VARCHAR(64), memory_type VARCHAR(64), "
                "status VARCHAR(32), confidence FLOAT, source VARCHAR(64), "
                "created_at TIMESTAMP, confirmed_at TIMESTAMP)"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO memory_sync_records "
                "(id, user_id, text, status) VALUES ('old-op', 'owner-1', 'legacy', 'queued')"
            )
        )
        await connection.run_sync(_migrate_memory_sync_records)
        migrated = (
            await connection.execute(
                text(
                    "SELECT status, idempotency_key, namespace, trashed_at, "
                    "permanent_delete_started_at "
                    "FROM memory_sync_records WHERE id = 'old-op'"
                )
            )
        ).one()

    assert migrated.status == "pending"
    assert migrated.idempotency_key == "old-op"
    assert migrated.namespace == "orhrex-personal-memory"
    assert migrated.trashed_at is None
    assert migrated.permanent_delete_started_at is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_existing_user_schema_gets_optional_trash_retention_policy():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE users (id VARCHAR(64) PRIMARY KEY, preferred_name VARCHAR(128))"
            )
        )
        await connection.execute(
            text("INSERT INTO users (id, preferred_name) VALUES ('owner-1', 'Test')")
        )
        await connection.run_sync(_migrate_user_memory_trash_retention)
        default_value = (
            await connection.execute(
                text(
                    "SELECT memory_trash_retention_days FROM users WHERE id = 'owner-1'"
                )
            )
        ).scalar_one()
        with pytest.raises(IntegrityError):
            await connection.execute(
                text(
                    "UPDATE users SET memory_trash_retention_days = 7 WHERE id = 'owner-1'"
                )
            )
        await connection.rollback()

    assert default_value is None
    await engine.dispose()
