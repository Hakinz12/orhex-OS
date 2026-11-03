"""HTTP integration tests for visitor session inspection, rotation, and revocation."""
import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1 import visitor as visitor_api
from app.core.auth import provision_user
from app.core.database import Base, _enable_sqlite_foreign_keys, get_db
from app.main import app
from app.models.domain import (
    AnonymousVisitorCredential,
    AnonymousVisitorIdentity,
    MemorySyncRecord,
    SuiInsight,
    User,
)
from app.services import anonymous_visitor_identity as visitor_service
from app.services.anonymous_visitor_identity import (
    VisitorIdentityDatabaseError,
    provision_visitor_identity,
    revoke_visitor_credential,
    resolve_visitor_credential,
)
from app.services.memory_persistence import namespace_for_user


@pytest.fixture
async def session_harness(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    original_limiter = visitor_api._session_rate_limiter
    monkeypatch.setattr(
        visitor_api,
        "_session_rate_limiter",
        visitor_api.SensitiveEndpointRateLimiter(requests_per_minute=10),
    )

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield SimpleNamespace(client=client, factory=session_factory)
    finally:
        app.dependency_overrides.pop(get_db, None)
        monkeypatch.setattr(visitor_api, "_session_rate_limiter", original_limiter)
        await engine.dispose()


async def _provision_visitor(harness):
    async with harness.factory() as session:
        return await provision_visitor_identity(session)


@pytest.mark.asyncio
async def test_visitor_session_metadata_exposes_safe_fields(session_harness):
    issue = await _provision_visitor(session_harness)
    response = await session_harness.client.get(
        "/api/v1/visitor/session",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == issue.identity.id
    assert payload["user_id"] == issue.user.id
    assert payload["status"] == "active"
    assert payload["created_at"] is not None
    assert payload["credential_created_at"] is not None
    assert payload["credential_expires_at"] is None
    assert "credential_digest" not in response.text
    assert issue.credential not in response.text


@pytest.mark.asyncio
async def test_repeated_session_inspection_does_not_provision_identity(
    session_harness,
):
    issue = await _provision_visitor(session_harness)
    for _ in range(3):
        response = await session_harness.client.get(
            "/api/v1/visitor/session",
            headers={"Authorization": f"Bearer {issue.credential}"},
        )
        assert response.status_code == 200
        assert response.json()["id"] == issue.identity.id

    async with session_harness.factory() as session:
        assert await session.scalar(
            select(func.count(AnonymousVisitorIdentity.id))
        ) == 1
        assert await session.scalar(select(func.count(User.id))) == 1
        assert await session.scalar(
            select(func.count(AnonymousVisitorCredential.id))
        ) == 1


@pytest.mark.asyncio
async def test_visitor_credential_rotation_and_revocation_are_isolated(session_harness):
    issue = await _provision_visitor(session_harness)
    original_namespace = namespace_for_user(issue.user.id)
    metadata = await session_harness.client.get(
        "/api/v1/visitor/session",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )
    assert metadata.status_code == 200

    rotated = await session_harness.client.post(
        "/api/v1/visitor/session/rotate",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )
    assert rotated.status_code == 200
    replacement = rotated.json()["visitor_credential"]
    replacement_digest = hashlib.sha256(
        replacement.encode("ascii")
    ).hexdigest()
    assert replacement.startswith("orv_")
    assert replacement != issue.credential
    assert set(rotated.json()) == {"visitor_credential"}
    assert replacement_digest not in rotated.text

    async with session_harness.factory() as session:
        resolution = await resolve_visitor_credential(session, replacement)
        assert resolution is not None
        assert resolution.identity.id == issue.identity.id
        assert resolution.user.id == issue.user.id
        assert namespace_for_user(resolution.user.id) == original_namespace

    replacement_session = await session_harness.client.get(
        "/api/v1/visitor/session",
        headers={"Authorization": f"Bearer {replacement}"},
    )
    assert replacement_session.status_code == 200
    assert replacement_session.json()["id"] == issue.identity.id
    assert replacement_session.json()["user_id"] == issue.user.id

    old_credential = await session_harness.client.get(
        "/api/v1/visitor/session",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )
    assert old_credential.status_code == 401

    revoked = await session_harness.client.delete(
        "/api/v1/visitor/session",
        headers={"Authorization": f"Bearer {replacement}"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    after_revocation = await session_harness.client.get(
        "/api/v1/visitor/session",
        headers={"Authorization": f"Bearer {replacement}"},
    )
    assert after_revocation.status_code == 401

    async with session_harness.factory() as session:
        records = (
            await session.execute(
                select(AnonymousVisitorCredential).where(
                    AnonymousVisitorCredential.visitor_id == issue.identity.id
                )
            )
        ).scalars().all()
    assert len(records) >= 2
    assert sum(record.revoked_at is None for record in records) == 0
    assert all(record.revoked_at is not None for record in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("credential_state", ["expired", "revoked"])
async def test_expired_or_revoked_credential_cannot_rotate(
    session_harness, credential_state
):
    issue = await _provision_visitor(session_harness)
    async with session_harness.factory() as session:
        record = (
            await session.execute(
                select(AnonymousVisitorCredential).where(
                    AnonymousVisitorCredential.visitor_id == issue.identity.id
                )
            )
        ).scalar_one()
        if credential_state == "expired":
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        else:
            assert await revoke_visitor_credential(session, issue.credential)

    before_count = await _credential_count(session_harness, issue.identity.id)
    response = await session_harness.client.post(
        "/api/v1/visitor/session/rotate",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )
    after_count = await _credential_count(session_harness, issue.identity.id)

    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"
    assert issue.credential not in response.text
    assert after_count == before_count == 1


async def _credential_count(harness, identity_id):
    async with harness.factory() as session:
        return await session.scalar(
            select(func.count(AnonymousVisitorCredential.id)).where(
                AnonymousVisitorCredential.visitor_id == identity_id
            )
        )


@pytest.mark.asyncio
async def test_visitor_a_cannot_inspect_or_modify_visitor_b_session(session_harness):
    visitor_a = await _provision_visitor(session_harness)
    visitor_b = await _provision_visitor(session_harness)

    inspected = await session_harness.client.get(
        "/api/v1/visitor/session",
        params={
            "visitor_id": visitor_b.identity.id,
            "user_id": visitor_b.user.id,
            "namespace": namespace_for_user(visitor_b.user.id),
        },
        headers={"Authorization": f"Bearer {visitor_a.credential}"},
    )
    assert inspected.status_code == 200
    assert inspected.json()["id"] == visitor_a.identity.id
    assert inspected.json()["id"] != visitor_b.identity.id

    rotated = await session_harness.client.post(
        "/api/v1/visitor/session/rotate",
        headers={"Authorization": f"Bearer {visitor_a.credential}"},
        json={
            "visitor_id": visitor_b.identity.id,
            "user_id": visitor_b.user.id,
            "namespace": namespace_for_user(visitor_b.user.id),
        },
    )
    assert rotated.status_code == 200
    assert rotated.json()["visitor_credential"] != visitor_b.credential

    revoked = await session_harness.client.delete(
        "/api/v1/visitor/session",
        headers={"Authorization": f"Bearer {rotated.json()['visitor_credential']}"},
        params={
            "visitor_id": visitor_b.identity.id,
            "user_id": visitor_b.user.id,
        },
    )
    assert revoked.status_code == 200
    b_session = await session_harness.client.get(
        "/api/v1/visitor/session",
        headers={"Authorization": f"Bearer {visitor_b.credential}"},
    )
    assert b_session.status_code == 200
    assert b_session.json()["id"] == visitor_b.identity.id
    assert b_session.json()["user_id"] == visitor_b.user.id


@pytest.mark.asyncio
async def test_revocation_preserves_visitor_user_and_memory_records(session_harness):
    issue = await _provision_visitor(session_harness)
    memory = MemorySyncRecord(
        id="visitor-memory-preservation",
        user_id=issue.user.id,
        namespace=namespace_for_user(issue.user.id),
        idempotency_key="visitor-memory-preservation-key",
        walrus_blob_id="existing-walrus-blob",
        walrus_memory_id="existing-walrus-memory",
        text="Existing visitor memory",
        status="confirmed",
    )
    async with session_harness.factory() as session:
        session.add(memory)
        await session.commit()

    response = await session_harness.client.delete(
        "/api/v1/visitor/session",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )
    assert response.status_code == 200

    async with session_harness.factory() as session:
        user = await session.get(User, issue.user.id)
        identity = await session.get(AnonymousVisitorIdentity, issue.identity.id)
        stored_memory = await session.get(MemorySyncRecord, memory.id)

    assert user is not None
    assert identity is not None
    assert identity.user_id == user.id
    assert stored_memory is not None
    assert stored_memory.user_id == user.id
    assert stored_memory.namespace == namespace_for_user(user.id)
    assert stored_memory.walrus_blob_id == "existing-walrus-blob"
    assert stored_memory.walrus_memory_id == "existing-walrus-memory"
    assert stored_memory.text == "Existing visitor memory"


@pytest.mark.asyncio
async def test_explicit_reset_creates_new_identity_without_moving_old_memory(
    session_harness,
):
    old = await _provision_visitor(session_harness)
    old_namespace = namespace_for_user(old.user.id)
    old_sui_address = "0x" + "a" * 64
    insight = SuiInsight(
        id="reset-preserved-sui-insight",
        user_id=old.user.id,
        sui_address=old_sui_address,
        network="mainnet",
    )
    memory = MemorySyncRecord(
        id="reset-preserved-memory",
        user_id=old.user.id,
        namespace=old_namespace,
        idempotency_key="reset-preserved-memory-key",
        walrus_blob_id="old-owner-blob",
        walrus_memory_id="old-owner-memory",
        text="Must remain with old identity",
        status="confirmed",
    )
    async with session_harness.factory() as session:
        old_user = await session.get(User, old.user.id)
        old_user.sui_address = old_sui_address
        session.add(memory)
        session.add(insight)
        await session.commit()

    response = await session_harness.client.post(
        "/api/v1/visitor/session/reset",
        headers={"Authorization": f"Bearer {old.credential}"},
    )

    assert response.status_code == 200
    assert set(response.json()) == {
        "visitor_identity_id",
        "visitor_credential",
    }
    new_identity_id = response.json()["visitor_identity_id"]
    new_credential = response.json()["visitor_credential"]
    assert new_identity_id != old.identity.id
    assert new_credential != old.credential
    new_credential_digest = hashlib.sha256(
        new_credential.encode("ascii")
    ).hexdigest()
    assert new_credential_digest not in response.text

    async with session_harness.factory() as session:
        new_visitor = await resolve_visitor_credential(session, new_credential)
        old_visitor = await resolve_visitor_credential(session, old.credential)
        old_identity = await session.get(
            AnonymousVisitorIdentity, old.identity.id
        )
        old_user = await session.get(User, old.user.id)
        new_memory_count = await session.scalar(
            select(func.count(MemorySyncRecord.id)).where(
                MemorySyncRecord.user_id == new_visitor.user.id
            )
        )
        stored_memory = await session.get(MemorySyncRecord, memory.id)
        stored_insight = await session.get(SuiInsight, insight.id)
        new_user = await session.get(User, new_visitor.user.id)

    assert new_visitor is not None
    assert new_visitor.identity.id == new_identity_id
    assert new_visitor.user.id != old.user.id
    assert namespace_for_user(new_visitor.user.id) != old_namespace
    assert old_visitor is None
    assert old_identity is not None
    assert old_user is not None
    assert old_user.sui_address == old_sui_address
    assert new_user is not None
    assert new_user.sui_address is None
    assert stored_memory is not None
    assert stored_memory.user_id == old.user.id
    assert stored_memory.namespace == old_namespace
    assert stored_memory.walrus_blob_id == "old-owner-blob"
    assert stored_memory.walrus_memory_id == "old-owner-memory"
    assert stored_insight is not None
    assert stored_insight.user_id == old.user.id
    assert stored_insight.sui_address == old_sui_address
    assert new_memory_count == 0


@pytest.mark.asyncio
async def test_reset_failure_rolls_back_old_credential_revocation(
    session_harness, monkeypatch
):
    old = await _provision_visitor(session_harness)
    monkeypatch.setattr(
        visitor_service,
        "_new_credential",
        lambda: old.credential,
    )

    response = await session_harness.client.post(
        "/api/v1/visitor/session/reset",
        headers={"Authorization": f"Bearer {old.credential}"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == "visitor_reset_unavailable"
    assert old.credential not in response.text
    assert "database" not in response.text.lower()
    async with session_harness.factory() as session:
        old_resolved = await resolve_visitor_credential(session, old.credential)
        identity_count = await session.scalar(
            select(func.count(AnonymousVisitorIdentity.id))
        )
        user_count = await session.scalar(select(func.count(User.id)))
    assert old_resolved is not None
    assert old_resolved.identity.id == old.identity.id
    assert identity_count == 1
    assert user_count == 1


@pytest.mark.asyncio
async def test_reset_rejects_revoked_or_expired_credentials(
    session_harness,
):
    revoked = await _provision_visitor(session_harness)
    expired = await _provision_visitor(session_harness)
    async with session_harness.factory() as session:
        assert await revoke_visitor_credential(session, revoked.credential)
        record = (
            await session.execute(
                select(AnonymousVisitorCredential).where(
                    AnonymousVisitorCredential.visitor_id == expired.identity.id
                )
            )
        ).scalar_one()
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    revoked_response = await session_harness.client.post(
        "/api/v1/visitor/session/reset",
        headers={"Authorization": f"Bearer {revoked.credential}"},
    )
    expired_response = await session_harness.client.post(
        "/api/v1/visitor/session/reset",
        headers={"Authorization": f"Bearer {expired.credential}"},
    )
    assert revoked_response.status_code == 401
    assert expired_response.status_code == 401
    async with session_harness.factory() as session:
        assert await session.scalar(
            select(func.count(AnonymousVisitorIdentity.id))
        ) == 2
        assert await session.scalar(select(func.count(User.id))) == 2


@pytest.mark.asyncio
async def test_concurrent_reset_with_same_credential_creates_one_new_identity(
    session_harness,
):
    old = await _provision_visitor(session_harness)
    reset_requests = (
        session_harness.client.post(
            "/api/v1/visitor/session/reset",
            headers={"Authorization": f"Bearer {old.credential}"},
        ),
        session_harness.client.post(
            "/api/v1/visitor/session/reset",
            headers={"Authorization": f"Bearer {old.credential}"},
        ),
    )
    first, second = await asyncio.gather(*reset_requests)
    successes = [response for response in (first, second) if response.status_code == 200]
    failures = [response for response in (first, second) if response.status_code != 200]
    assert all(response.status_code in {200, 401, 503} for response in (first, second))
    async with session_harness.factory() as session:
        old_visitor = await resolve_visitor_credential(session, old.credential)
        identity_count = await session.scalar(
            select(func.count(AnonymousVisitorIdentity.id))
        )
        user_count = await session.scalar(select(func.count(User.id)))

    if successes:
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].status_code in {401, 503}
        new_credential = successes[0].json()["visitor_credential"]
        async with session_harness.factory() as session:
            new_visitor = await resolve_visitor_credential(session, new_credential)
        assert new_visitor is not None
        assert new_visitor.identity.id != old.identity.id
        assert old_visitor is None
        assert identity_count == 2
        assert user_count == 2
    else:
        assert len(failures) == 2
        assert all(response.status_code in {401, 503} for response in failures)
        assert old_visitor is not None
        assert old_visitor.identity.id == old.identity.id
        assert identity_count == 1
        assert user_count == 1


@pytest.mark.asyncio
async def test_reset_rejects_pat_and_does_not_change_pat_authentication(
    session_harness,
):
    async with session_harness.factory() as session:
        user, token = await provision_user(session)

    response = await session_harness.client.post(
        "/api/v1/visitor/session/reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"
    async with session_harness.factory() as session:
        from app.core.auth import resolve_bearer_user

        assert (await resolve_bearer_user(session, token)).id == user.id


@pytest.mark.asyncio
async def test_reset_rejects_client_supplied_ownership_fields_without_changes(
    session_harness,
):
    old = await _provision_visitor(session_harness)
    response = await session_harness.client.post(
        "/api/v1/visitor/session/reset",
        headers={"Authorization": f"Bearer {old.credential}"},
        json={
            "user_id": "attacker-user",
            "visitor_id": "attacker-visitor",
            "namespace": "shared-default",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    async with session_harness.factory() as session:
        old_visitor = await resolve_visitor_credential(session, old.credential)
        assert await session.scalar(
            select(func.count(AnonymousVisitorIdentity.id))
        ) == 1
        assert await session.scalar(select(func.count(User.id))) == 1
    assert old_visitor is not None
    assert old_visitor.identity.id == old.identity.id


@pytest.mark.asyncio
async def test_invalid_session_errors_are_generic_and_do_not_leak_details(
    session_harness,
):
    invalid_credential = "orv_" + ("z" * 43)
    responses = await asyncio.gather(
        session_harness.client.get(
            "/api/v1/visitor/session",
            headers={"Authorization": f"Bearer {invalid_credential}"},
        ),
        session_harness.client.post(
            "/api/v1/visitor/session/rotate",
            headers={"Authorization": f"Bearer {invalid_credential}"},
        ),
        session_harness.client.delete(
            "/api/v1/visitor/session",
            headers={"Authorization": f"Bearer {invalid_credential}"},
        ),
    )

    for response in responses:
        assert response.status_code == 401
        assert response.json()["error"] == "unauthenticated"
        assert invalid_credential not in response.text
        assert "digest" not in response.text
        assert "database" not in response.text.lower()


@pytest.mark.asyncio
async def test_rotation_database_errors_are_sanitized(
    session_harness, monkeypatch
):
    issue = await _provision_visitor(session_harness)

    async def fail_rotation(_session, _credential):
        raise VisitorIdentityDatabaseError(
            "database-url and credential digest must not escape"
        )

    monkeypatch.setattr(visitor_api, "rotate_visitor_credential", fail_rotation)
    response = await session_harness.client.post(
        "/api/v1/visitor/session/rotate",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == "visitor_session_unavailable"
    assert issue.credential not in response.text
    assert "database-url" not in response.text
    assert "digest" not in response.text


@pytest.mark.asyncio
async def test_session_operations_do_not_log_credentials_or_digests(
    session_harness, caplog
):
    caplog.set_level(logging.INFO)
    issue = await _provision_visitor(session_harness)
    rotated = await session_harness.client.post(
        "/api/v1/visitor/session/rotate",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )
    replacement = rotated.json()["visitor_credential"]
    await session_harness.client.delete(
        "/api/v1/visitor/session",
        headers={"Authorization": f"Bearer {replacement}"},
    )
    credentials = (issue.credential, replacement)
    digests = tuple(
        hashlib.sha256(credential.encode("ascii")).hexdigest()
        for credential in credentials
    )

    for secret in (*credentials, *digests):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_pat_credentials_do_not_authenticate_to_visitor_session_endpoints(
    session_harness,
):
    async with session_harness.factory() as session:
        user, token = await provision_user(session)
    for method, path in (
        ("GET", "/api/v1/visitor/session"),
        ("POST", "/api/v1/visitor/session/rotate"),
        ("DELETE", "/api/v1/visitor/session"),
    ):
        response = await session_harness.client.request(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401
        assert response.json()["error"] == "unauthenticated"


@pytest.mark.asyncio
async def test_visitor_session_apis_keep_identity_and_user_persistent(session_harness):
    issue = await _provision_visitor(session_harness)
    initial = await session_harness.client.get(
        "/api/v1/visitor/session",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )
    rotated = await session_harness.client.post(
        "/api/v1/visitor/session/rotate",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )

    assert initial.status_code == 200
    assert rotated.status_code == 200
    assert initial.json()["id"] == issue.identity.id
    assert initial.json()["user_id"] == issue.user.id
    assert rotated.json()["visitor_credential"].startswith("orv_")
