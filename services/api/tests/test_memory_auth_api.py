"""Authentication, ownership, and HTTP contract tests for memory routes."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.walrus.memwal import WalrusAdapterError
from app.api.v1 import memory as memory_api
from app.core.auth import provision_user, revoke_user_token
from app.core.database import (
    Base,
    _enable_sqlite_foreign_keys,
    get_db,
)
from app.main import app
from app.core.rate_limit import SensitiveEndpointRateLimiter
from app.core.config import settings
from app.models.domain import User, UserAccessToken
from app.services.memory_persistence import namespace_for_user


class FakeWalrus:
    def __init__(self):
        self.write_result = {
            "status": "confirmed",
            "blob_id": "blob-123",
            "memory_id": "memory-123",
        }
        self.reconciliation_result = {
            "status": "done",
            "blob_id": "blob-reconciled",
            "namespace": None,
            "memory_id": "memory-reconciled",
        }
        self.health_result = {"status": "ready", "write_ready": True}
        self.remember = AsyncMock(side_effect=self._remember)
        self.recall = AsyncMock(return_value=[])
        self.get_remember_status = AsyncMock(side_effect=self._get_status)
        self.check_readiness = AsyncMock(return_value=self.health_result)

    async def _remember(self, **kwargs):
        if isinstance(self.write_result, Exception):
            raise self.write_result
        return {**self.write_result, "namespace": kwargs["namespace"]}

    async def _get_status(self, _job_id):
        return self.reconciliation_result


@pytest.fixture
async def api_harness():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    walrus = FakeWalrus()
    original_walrus = memory_api._walrus
    from app import main as main_module

    original_limiter = main_module._sensitive_rate_limiter
    main_module._sensitive_rate_limiter = SensitiveEndpointRateLimiter(
        requests_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE
    )
    memory_api._walrus = walrus

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield SimpleNamespace(
                client=client,
                factory=session_factory,
                walrus=walrus,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        memory_api._walrus = original_walrus
        main_module._sensitive_rate_limiter = original_limiter
        await engine.dispose()


async def provision(harness, *, preferred_name=None, expires_at=None):
    async with harness.factory() as session:
        user, token = await provision_user(
            session,
            preferred_name=preferred_name,
            expires_at=expires_at,
        )
        return user.id, token


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_authentication_is_required_for_memory_and_status_operations(api_harness):
    missing = await api_harness.client.get("/api/v1/memory/status")
    invalid = await api_harness.client.post(
        "/api/v1/memory/recall",
        json={"query": "hello"},
        headers=bearer("orh_invalid-token"),
    )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert invalid.status_code == 401
    assert missing.json()["error"] == "unauthenticated"
    api_harness.walrus.recall.assert_not_awaited()


@pytest.mark.asyncio
async def test_provisioning_is_not_exposed_as_a_public_api(api_harness):
    response = await api_harness.client.post(
        "/api/v1/auth/provision",
        json={"preferred_name": "unauthorized"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_memory_api_requests_are_rate_limited(api_harness, monkeypatch):
    from app import main as main_module

    limiter = SensitiveEndpointRateLimiter(requests_per_minute=2)
    monkeypatch.setattr(main_module, "_sensitive_rate_limiter", limiter)
    first = await api_harness.client.get("/api/v1/memory/status")
    second = await api_harness.client.get("/api/v1/memory/status")
    third = await api_harness.client.get("/api/v1/memory/status")

    assert first.status_code == 401
    assert second.status_code == 401
    assert third.status_code == 429
    assert third.headers.get("retry-after")
    assert third.json()["error"] == "rate_limited"


@pytest.mark.asyncio
async def test_provisioned_bearer_authenticates_and_token_is_not_stored(api_harness):
    user_id, token = await provision(api_harness, preferred_name="Alice")
    response = await api_harness.client.get(
        "/api/v1/memory/status",
        headers=bearer(token),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    async with api_harness.factory() as session:
        credentials = (await session.execute(select(UserAccessToken))).scalars().all()
        user = await session.get(User, user_id)
        assert len(credentials) == 1
        assert token not in credentials[0].token_digest
        assert user.preferred_name == "Alice"


@pytest.mark.asyncio
async def test_revoked_and_expired_tokens_are_rejected(api_harness):
    user_id, token = await provision(api_harness)
    async with api_harness.factory() as session:
        credential = (await session.execute(select(UserAccessToken))).scalar_one()
        assert await revoke_user_token(
            session, user_id=user_id, token_id=credential.id
        )
    revoked = await api_harness.client.get(
        "/api/v1/memory/status", headers=bearer(token)
    )
    assert revoked.status_code == 401

    expired_user_id, expired_token = await provision(api_harness)
    async with api_harness.factory() as session:
        expired_credential = (
            await session.execute(
                select(UserAccessToken).where(
                    UserAccessToken.user_id == expired_user_id
                )
            )
        ).scalar_one()
        expired_credential.expires_at = datetime.now(timezone.utc) - timedelta(
            seconds=1
        )
        await session.commit()
    expired = await api_harness.client.get(
        "/api/v1/memory/status", headers=bearer(expired_token)
    )
    assert expired.status_code == 401


@pytest.mark.asyncio
async def test_newly_provisioned_token_has_mandatory_default_expiration(api_harness):
    _, token = await provision(api_harness)
    async with api_harness.factory() as session:
        credential = (await session.execute(select(UserAccessToken))).scalar_one()
    assert credential.expires_at is not None
    expiration = credential.expires_at
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=timezone.utc)
    assert expiration > datetime.now(timezone.utc)
    response = await api_harness.client.get(
        "/api/v1/memory/status",
        headers=bearer(token),
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_memory_creation_and_recall_are_scoped_to_authenticated_principal(api_harness):
    user_id, token = await provision(api_harness)
    response = await api_harness.client.post(
        "/api/v1/memory/remember",
        headers=bearer(token),
        json={
            "text": "The user prefers concise notifications.",
            "category": "personal",
            "memory_type": "preference",
            "metadata": {"source": "test"},
        },
    )

    assert response.status_code == 201
    operation = response.json()
    assert operation["status"] == "confirmed"
    assert operation["namespace"] == namespace_for_user(user_id)
    assert "user_id" not in operation
    assert api_harness.walrus.remember.await_args.kwargs["namespace"] == operation["namespace"]
    api_harness.walrus.recall.return_value = [
        {
            "blob_id": "blob-123",
            "text": "The user prefers concise notifications.",
            "distance": 0.1,
            "score": 0.9,
            "created_at": None,
            "namespace": operation["namespace"],
        }
    ]

    recalled = await api_harness.client.post(
        "/api/v1/memory/recall",
        headers=bearer(token),
        json={"query": "notification preferences"},
    )
    assert recalled.status_code == 200
    assert recalled.json()["namespace"] == operation["namespace"]
    assert api_harness.walrus.recall.await_args.kwargs["namespace"] == operation["namespace"]


@pytest.mark.asyncio
async def test_client_cannot_select_another_users_id_or_namespace(api_harness):
    user_id, token = await provision(api_harness)
    bad_owner = await api_harness.client.post(
        "/api/v1/memory/remember",
        headers=bearer(token),
        json={"text": "Not accepted.", "user_id": "another-user"},
    )
    bad_namespace = await api_harness.client.post(
        "/api/v1/memory/recall",
        headers=bearer(token),
        json={"query": "private fact", "namespace": "another-users-namespace"},
    )
    assert bad_owner.status_code == 422
    assert bad_namespace.status_code == 422
    assert api_harness.walrus.remember.await_count == 0
    assert api_harness.walrus.recall.await_count == 0

    created = await api_harness.client.post(
        "/api/v1/memory/remember",
        headers=bearer(token),
        json={"text": "Owned by the first user."},
    )
    record_id = created.json()["operation_id"]
    _, other_token = await provision(api_harness)
    cross_user = await api_harness.client.get(
        f"/api/v1/memory/operations/{record_id}",
        headers=bearer(other_token),
    )
    assert cross_user.status_code == 404
    assert cross_user.json()["error"] == "not_found"
    assert created.json()["namespace"] == namespace_for_user(user_id)


@pytest.mark.asyncio
async def test_write_failures_and_uncertain_outcomes_have_explicit_responses(api_harness):
    _, token = await provision(api_harness)
    api_harness.walrus.write_result = {
        "status": "failed",
        "error": "Walrus job failed.",
        "error_type": "MemWalRememberJobFailed",
        "definite_failure": True,
        "job_id": "failed-job",
    }
    failed = await api_harness.client.post(
        "/api/v1/memory/remember",
        headers=bearer(token),
        json={"text": "A definitely failed write."},
    )
    assert failed.status_code == 502
    assert failed.json()["status"] == "failed"

    api_harness.walrus.write_result = {
        "status": "timeout",
        "error": "Walrus confirmation timed out.",
        "job_id": "pending-job",
    }
    uncertain = await api_harness.client.post(
        "/api/v1/memory/remember",
        headers=bearer(token),
        json={"text": "A possibly accepted write."},
    )
    assert uncertain.status_code == 202
    assert uncertain.json()["status"] == "reconciliation_required"

    operation_id = uncertain.json()["operation_id"]
    api_harness.walrus.reconciliation_result = {
        "status": "done",
        "blob_id": "blob-after-reconcile",
        "namespace": uncertain.json()["namespace"],
        "memory_id": "memory-after-reconcile",
    }
    reconciled = await api_harness.client.post(
        f"/api/v1/memory/operations/{operation_id}/reconcile",
        headers=bearer(token),
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == "confirmed"
    assert api_harness.walrus.remember.await_count == 2
    api_harness.walrus.get_remember_status.assert_awaited_once_with("pending-job")


@pytest.mark.asyncio
async def test_recall_failure_uses_gateway_error_contract(api_harness):
    _, token = await provision(api_harness)
    api_harness.walrus.recall.side_effect = WalrusAdapterError("Walrus unavailable")

    response = await api_harness.client.post(
        "/api/v1/memory/recall",
        headers=bearer(token),
        json={"query": "a relevant memory"},
    )

    assert response.status_code == 502
    assert response.json()["error"] == "walrus_recall_failed"


@pytest.mark.asyncio
async def test_database_write_failure_is_reported_without_walrus_call(api_harness, monkeypatch):
    _, token = await provision(api_harness)
    original_commit = AsyncSession.commit

    async def fail_commit(_session):
        raise SQLAlchemyError("injected database failure")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    response = await api_harness.client.post(
        "/api/v1/memory/remember",
        headers=bearer(token),
        json={"text": "Local write must fail before Walrus."},
    )
    monkeypatch.setattr(AsyncSession, "commit", original_commit)

    assert response.status_code == 503
    assert response.json()["error"] == "database_error"
    api_harness.walrus.remember.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsupported_delete_checks_ownership_and_reports_not_implemented(api_harness):
    _, token = await provision(api_harness)
    created = await api_harness.client.post(
        "/api/v1/memory/remember",
        headers=bearer(token),
        json={"text": "A locally owned memory."},
    )
    result = await api_harness.client.delete(
        f"/api/v1/memory/operations/{created.json()['operation_id']}",
        headers=bearer(token),
    )
    assert result.status_code == 501
    assert result.json()["error"] == "unsupported_operation"


@pytest.mark.asyncio
async def test_request_validation_and_response_contract(api_harness):
    _, token = await provision(api_harness)
    short_text = await api_harness.client.post(
        "/api/v1/memory/remember",
        headers=bearer(token),
        json={"text": "x"},
    )
    bad_limit = await api_harness.client.post(
        "/api/v1/memory/recall",
        headers=bearer(token),
        json={"query": "valid query", "limit": 100},
    )
    assert short_text.status_code == 422
    assert bad_limit.status_code == 422

    confirmed = await api_harness.client.post(
        "/api/v1/memory/remember",
        headers=bearer(token),
        json={"text": "Response has a stable operation schema."},
    )
    assert set(confirmed.json()) == {
        "status",
        "operation_id",
        "blob_id",
        "memory_id",
        "namespace",
        "category",
        "memory_type",
        "metadata",
        "created_at",
        "confirmed_at",
        "error",
    }


@pytest.mark.asyncio
async def test_walrus_adapter_exception_is_reconciliation_required(api_harness):
    _, token = await provision(api_harness)
    api_harness.walrus.write_result = ConnectionError("connection lost")
    response = await api_harness.client.post(
        "/api/v1/memory/remember",
        headers=bearer(token),
        json={"text": "Write might have reached Walrus."},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "reconciliation_required"


@pytest.mark.asyncio
async def test_operation_status_requires_authentication(api_harness):
    response = await api_harness.client.get(
        "/api/v1/memory/operations/not-a-real-record"
    )
    assert response.status_code == 401
