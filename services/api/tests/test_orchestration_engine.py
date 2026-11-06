import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import provision_user
from app.core.database import Base, _enable_sqlite_foreign_keys, get_db
from app.main import app
from app.models.domain import ActionProposal, Commitment, OrchestrationRun, Task, User
from app.services import orchestration_engine
from app.services.orchestration_engine import run_orchestration


@pytest.fixture
async def db_session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'orchestration.db'}")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_orchestration_refreshes_signals_and_returns_approval_gated_next_steps(db_session):
    now = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
    db_session.add_all([User(id="owner-1")])
    await db_session.flush()
    db_session.add_all([
        Commitment(id="commitment-1", user_id="owner-1", text="Submit ORHEX demo", due_date=now + timedelta(days=1), is_completed=False),
        Task(id="task-1", user_id="owner-1", title="Fix API regression", priority="high", status="todo", due_at=now - timedelta(hours=2)),
    ])
    await db_session.commit()

    record = await run_orchestration(db_session, user_id="owner-1", idempotency_key="cycle-1", now=now)
    import json
    summary = json.loads(record.summary_json)
    assert summary["decision"] == "approval_required"
    assert summary["counts"]["overdue_tasks"] == 1
    assert summary["counts"]["pending_approvals"] == 1
    assert summary["safety"]["external_actions_executed"] == 0
    assert any(item["kind"] == "overdue_task" for item in summary["recommendations"])
    assert any(item["requires_approval"] for item in summary["recommendations"])
    assert await db_session.scalar(select(ActionProposal).where(ActionProposal.user_id == "owner-1")) is not None


@pytest.mark.asyncio
async def test_orchestration_idempotency_and_owner_isolation(db_session):
    db_session.add_all([User(id="owner-a"), User(id="owner-b")])
    await db_session.flush()
    db_session.add_all([
        Task(id="owner-a-task", user_id="owner-a", title="A task", status="todo"),
        Task(id="owner-b-task", user_id="owner-b", title="B task", status="todo"),
    ])
    await db_session.commit()
    first = await run_orchestration(db_session, user_id="owner-a", idempotency_key="same-key")
    again = await run_orchestration(db_session, user_id="owner-a", idempotency_key="same-key")
    other = await run_orchestration(db_session, user_id="owner-b", idempotency_key="same-key")
    assert first.id == again.id
    assert first.id != other.id
    import json
    assert json.loads(first.summary_json)["counts"]["open_tasks"] == 1
    assert json.loads(other.summary_json)["counts"]["open_tasks"] == 1
    assert len(list((await db_session.scalars(select(OrchestrationRun))).all())) == 2


@pytest.mark.asyncio
async def test_concurrent_orchestration_with_same_key_returns_one_run(db_session):
    db_session.add(User(id="concurrent-owner"))
    await db_session.commit()
    engine = db_session.bind
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )

    async def execute():
        async with factory() as session:
            return await run_orchestration(
                session,
                user_id="concurrent-owner",
                idempotency_key="concurrent-cycle",
            )

    first, second = await asyncio.gather(execute(), execute())
    assert first.id == second.id
    assert await db_session.scalar(select(OrchestrationRun.id).where(
        OrchestrationRun.user_id == "concurrent-owner",
        OrchestrationRun.idempotency_key == "concurrent-cycle",
    )) == first.id


@pytest.mark.asyncio
async def test_orchestration_failure_rolls_back_all_cycle_writes(db_session, monkeypatch):
    now = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
    db_session.add(User(id="failure-owner"))
    await db_session.flush()
    db_session.add(Commitment(
        id="failure-commitment",
        user_id="failure-owner",
        text="Review release",
        due_date=now + timedelta(days=1),
        is_completed=False,
    ))
    await db_session.commit()

    async def fail_after_insight_refresh(*_args, **_kwargs):
        raise RuntimeError("injected proposal failure")

    monkeypatch.setattr(
        orchestration_engine,
        "prepare_action_proposals",
        fail_after_insight_refresh,
    )
    with pytest.raises(RuntimeError, match="injected proposal failure"):
        await run_orchestration(
            db_session,
            user_id="failure-owner",
            idempotency_key="failed-cycle",
            now=now,
        )

    assert await db_session.scalar(select(OrchestrationRun).where(
        OrchestrationRun.user_id == "failure-owner",
    )) is None
    assert await db_session.scalar(select(ActionProposal).where(
        ActionProposal.user_id == "failure-owner",
    )) is None
    from app.models.domain import AnticipationInsight

    assert await db_session.scalar(select(AnticipationInsight).where(
        AnticipationInsight.user_id == "failure-owner",
    )) is None


@pytest.mark.asyncio
async def test_orchestration_api_requires_auth_and_returns_run_history(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'orchestration-api.db'}")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async def override_db():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    async with factory() as db:
        session = await provision_user(db)
    headers = {"Authorization": f"Bearer {session[1]}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post("/api/v1/orchestration/run", json={"idempotency_key": "c4c09d2d-9b92-4b5a-9b3d-58c1a2b7b1b0"})
        assert denied.status_code == 401
        response = await client.post("/api/v1/orchestration/run", json={"idempotency_key": "c4c09d2d-9b92-4b5a-9b3d-58c1a2b7b1b0"}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["summary"]["decision"] == "all_clear"
        latest = await client.get("/api/v1/orchestration/latest", headers=headers)
        history = await client.get("/api/v1/orchestration/runs", headers=headers)
        assert latest.status_code == 200 and latest.json()["id"] == response.json()["id"]
        assert history.status_code == 200 and len(history.json()) == 1
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()
