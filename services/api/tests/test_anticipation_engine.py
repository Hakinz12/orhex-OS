import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import provision_user
from app.core.database import Base, _enable_sqlite_foreign_keys, get_db
from app.main import app
from app.models.domain import AnticipationInsight, Commitment, Goal, User
from app.services.anonymous_visitor_identity import provision_visitor_identity
from app.services.anticipation_engine import refresh_anticipations, set_anticipation_status


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add_all([User(id="owner-a"), User(id="owner-b")])
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
async def anticipation_api_harness(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'anticipation-api.db'}"
    )
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        async with factory() as session:
            owner, owner_token = await provision_user(session)
        async with factory() as session:
            other, other_token = await provision_user(session)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield SimpleNamespace(
                client=client,
                factory=factory,
                owner_id=owner.id,
                owner_token=owner_token,
                other_id=other.id,
                other_token=other_token,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _insight(user_id, fingerprint):
    return AnticipationInsight(
        user_id=user_id,
        fingerprint=fingerprint,
        kind="commitment_due_soon",
        source_type="commitment",
        source_id=f"commitment-{fingerprint[:8]}",
        title="Review release",
        explanation="A commitment is due soon.",
        priority="medium",
        evidence_json='{"source":"test"}',
        status="active",
    )


@pytest.mark.asyncio
async def test_due_commitment_generates_explainable_insight_and_deduplicates(db_session):
    now = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
    commitment = Commitment(
        id="commitment-a", user_id="owner-a", text="Submit ORHEX demo",
        due_date=now + timedelta(days=2), is_completed=False,
    )
    db_session.add(commitment)
    await db_session.commit()

    first = await refresh_anticipations(db_session, user_id="owner-a", now=now)
    second = await refresh_anticipations(db_session, user_id="owner-a", now=now)

    assert len(first) == len(second) == 1
    assert first[0].kind == "commitment_due_soon"
    assert first[0].priority == "medium"
    assert "seven days" in first[0].explanation
    assert first[0].evidence_json.find("commitment-a") >= 0
    rows = list((await db_session.scalars(select(AnticipationInsight))).all())
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_overdue_goal_generates_high_priority_insight(db_session):
    now = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
    db_session.add(Goal(
        id="goal-a", user_id="owner-a", title="Launch ORHEX",
        target_date=now - timedelta(days=1), status="active", progress_percent=40,
    ))
    await db_session.commit()

    items = await refresh_anticipations(db_session, user_id="owner-a", now=now)
    assert len(items) == 1
    assert items[0].kind == "goal_overdue"
    assert items[0].priority == "high"


@pytest.mark.asyncio
async def test_dismissal_is_owner_scoped_and_not_resurrected(db_session):
    now = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
    db_session.add(Commitment(
        id="commitment-a", user_id="owner-a", text="Review PR",
        due_date=now + timedelta(days=1), is_completed=False,
    ))
    await db_session.commit()
    items = await refresh_anticipations(db_session, user_id="owner-a", now=now)
    insight = items[0]

    assert await set_anticipation_status(
        db_session, user_id="owner-b", insight_id=insight.id, status="dismissed"
    ) is None
    dismissed = await set_anticipation_status(
        db_session, user_id="owner-a", insight_id=insight.id, status="dismissed"
    )
    assert dismissed.status == "dismissed"
    assert await refresh_anticipations(db_session, user_id="owner-a", now=now) == []


@pytest.mark.asyncio
async def test_resolved_insight_reactivates_with_current_evidence_but_dismissed_does_not(
    db_session,
):
    now = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
    commitment = Commitment(
        id="commitment-recurrence", user_id="owner-a", text="Review release",
        due_date=now + timedelta(days=1), is_completed=False,
    )
    db_session.add(commitment)
    await db_session.commit()

    active = (await refresh_anticipations(
        db_session, user_id="owner-a", now=now,
    ))[0]
    original_created_at = active.created_at
    commitment.due_date = now + timedelta(days=10)
    await db_session.commit()
    assert await refresh_anticipations(
        db_session, user_id="owner-a", now=now + timedelta(days=1),
    ) == []
    resolved = await db_session.scalar(select(AnticipationInsight).where(
        AnticipationInsight.id == active.id,
    ))
    assert resolved.status == "resolved"

    commitment.due_date = now + timedelta(days=2)
    commitment.text = "Review updated release"
    await db_session.commit()
    reactivated_at = now + timedelta(hours=3)
    reactivated = (await refresh_anticipations(
        db_session, user_id="owner-a", now=reactivated_at,
    ))[0]
    assert reactivated.id == active.id
    assert reactivated.fingerprint == active.fingerprint
    assert reactivated.status == "active"
    assert reactivated.created_at == original_created_at
    assert reactivated.updated_at == reactivated_at
    assert "updated release" in reactivated.evidence_json

    await set_anticipation_status(
        db_session, user_id="owner-a", insight_id=active.id, status="dismissed",
    )
    commitment.due_date = now + timedelta(days=10)
    await db_session.commit()
    await refresh_anticipations(db_session, user_id="owner-a", now=now)
    commitment.due_date = now + timedelta(days=2)
    await db_session.commit()
    assert await refresh_anticipations(
        db_session, user_id="owner-a", now=now,
    ) == []
    dismissed = await db_session.scalar(select(AnticipationInsight).where(
        AnticipationInsight.id == active.id,
    ))
    assert dismissed.status == "dismissed"


@pytest.mark.asyncio
async def test_anticipation_endpoints_require_authentication(anticipation_api_harness):
    client = anticipation_api_harness.client
    requests = [
        client.get("/api/v1/anticipation"),
        client.post("/api/v1/anticipation/refresh"),
        client.patch(
            "/api/v1/anticipation/not-a-real-insight",
            json={"status": "dismissed"},
        ),
    ]

    responses = await asyncio.gather(*requests)

    assert [response.status_code for response in responses] == [401, 401, 401]


@pytest.mark.asyncio
async def test_anticipation_api_is_owner_scoped_and_supports_authorized_lifecycle(
    anticipation_api_harness,
):
    async with anticipation_api_harness.factory() as db:
        own = _insight(anticipation_api_harness.owner_id, "a" * 64)
        other = _insight(anticipation_api_harness.other_id, "b" * 64)
        db.add_all([own, other])
        await db.commit()
        own_id = own.id
        other_id = other.id

    client = anticipation_api_harness.client
    owner_headers = _auth(anticipation_api_harness.owner_token)
    own_list = await client.get("/api/v1/anticipation", headers=owner_headers)
    assert own_list.status_code == 200
    assert [item["id"] for item in own_list.json()] == [own_id]

    cross_user_list = await client.get(
        "/api/v1/anticipation?include_inactive=true",
        headers=owner_headers,
    )
    assert cross_user_list.status_code == 200
    assert other_id not in {item["id"] for item in cross_user_list.json()}

    cross_user_update = await client.patch(
        f"/api/v1/anticipation/{other_id}",
        json={"status": "dismissed"},
        headers=owner_headers,
    )
    assert cross_user_update.status_code == 404
    async with anticipation_api_harness.factory() as db:
        untouched = await db.scalar(
            select(AnticipationInsight).where(AnticipationInsight.id == other_id)
        )
        assert untouched.status == "active"

    dismissed = await client.patch(
        f"/api/v1/anticipation/{own_id}",
        json={"status": "dismissed"},
        headers=owner_headers,
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"
    active_only = await client.get("/api/v1/anticipation", headers=owner_headers)
    assert active_only.status_code == 200
    assert active_only.json() == []
    include_inactive = await client.get(
        "/api/v1/anticipation?include_inactive=true",
        headers=owner_headers,
    )
    assert include_inactive.status_code == 200
    assert include_inactive.json()[0]["status"] == "dismissed"


@pytest.mark.asyncio
async def test_anticipation_api_accepts_authenticated_visitor_sessions(
    anticipation_api_harness,
):
    async with anticipation_api_harness.factory() as db:
        visitor = await provision_visitor_identity(db)
        db.add(_insight(visitor.user.id, "c" * 64))
        await db.commit()

    response = await anticipation_api_harness.client.get(
        "/api/v1/anticipation",
        headers=_auth(visitor.credential),
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["status"] == "active"
