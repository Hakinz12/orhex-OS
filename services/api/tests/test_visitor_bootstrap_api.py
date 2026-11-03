"""HTTP integration tests for anonymous visitor bootstrap."""
import hashlib
import logging
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1 import visitor as visitor_api
from app.core.auth import provision_user, resolve_bearer_user
from app.core.database import Base, _enable_sqlite_foreign_keys, get_db
from app.core.rate_limit import SensitiveEndpointRateLimiter
from app.main import app
from app.models.domain import (
    AnonymousVisitorCredential,
    AnonymousVisitorIdentity,
    User,
)
from app.services.anonymous_visitor_identity import resolve_visitor_credential
from app.services.memory_persistence import namespace_for_user


@pytest.fixture
async def bootstrap_harness(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    original_limiter = visitor_api._bootstrap_rate_limiter
    monkeypatch.setattr(
        visitor_api,
        "_bootstrap_rate_limiter",
        SensitiveEndpointRateLimiter(requests_per_minute=5),
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
        monkeypatch.setattr(visitor_api, "_bootstrap_rate_limiter", original_limiter)
        await engine.dispose()


async def _counts(harness):
    async with harness.factory() as session:
        return (
            await session.scalar(select(func.count(User.id))),
            await session.scalar(
                select(func.count(AnonymousVisitorIdentity.id))
            ),
            await session.scalar(
                select(func.count(AnonymousVisitorCredential.id))
            ),
        )


@pytest.mark.asyncio
async def test_bootstrap_creates_and_returns_resolvable_visitor_credential(
    bootstrap_harness,
):
    response = await bootstrap_harness.client.post(
        "/api/v1/visitor/bootstrap"
    )

    assert response.status_code == 200
    assert set(response.json()) == {"visitor_credential"}
    credential = response.json()["visitor_credential"]
    async with bootstrap_harness.factory() as session:
        resolved = await resolve_visitor_credential(session, credential)
    user_count, identity_count, credential_count = await _counts(bootstrap_harness)

    assert resolved is not None
    assert resolved.identity.user_id == resolved.user.id
    assert (user_count, identity_count, credential_count) == (1, 1, 1)


@pytest.mark.asyncio
async def test_independent_bootstraps_have_distinct_owners_and_namespaces(
    bootstrap_harness,
):
    first = await bootstrap_harness.client.post("/api/v1/visitor/bootstrap")
    second = await bootstrap_harness.client.post(
        "/api/v1/visitor/bootstrap",
        headers={"X-Forwarded-For": "198.51.100.77"},
    )
    async with bootstrap_harness.factory() as session:
        first_resolved = await resolve_visitor_credential(
            session, first.json()["visitor_credential"]
        )
        second_resolved = await resolve_visitor_credential(
            session, second.json()["visitor_credential"]
        )

    assert first_resolved is not None
    assert second_resolved is not None
    assert first_resolved.identity.id != second_resolved.identity.id
    assert first_resolved.user.id != second_resolved.user.id
    assert first.json()["visitor_credential"] != second.json()["visitor_credential"]
    first_namespace = namespace_for_user(first_resolved.user.id)
    second_namespace = namespace_for_user(second_resolved.user.id)
    assert first_namespace != second_namespace
    assert "user_default" not in {
        first_resolved.user.id,
        second_resolved.user.id,
        first_namespace,
        second_namespace,
    }


@pytest.mark.asyncio
async def test_bootstrap_response_and_logs_exclude_digests_and_internal_fields(
    bootstrap_harness, caplog
):
    caplog.set_level(logging.INFO)
    response = await bootstrap_harness.client.post("/api/v1/visitor/bootstrap")
    credential = response.json()["visitor_credential"]
    digest = hashlib.sha256(credential.encode("ascii")).hexdigest()

    assert set(response.json()) == {"visitor_credential"}
    assert digest not in response.text
    assert credential not in caplog.text
    assert digest not in caplog.text
    for forbidden in ("user_id", "identity_id", "namespace", "database"):
        assert forbidden not in response.json()


@pytest.mark.asyncio
async def test_bootstrap_rate_limit_blocks_without_creating_another_identity(
    bootstrap_harness, monkeypatch
):
    monkeypatch.setattr(
        visitor_api,
        "_bootstrap_rate_limiter",
        SensitiveEndpointRateLimiter(requests_per_minute=1),
    )
    first = await bootstrap_harness.client.post("/api/v1/visitor/bootstrap")
    second = await bootstrap_harness.client.post("/api/v1/visitor/bootstrap")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"] == "rate_limited"
    assert "Retry-After" in second.headers
    assert (await _counts(bootstrap_harness)) == (1, 1, 1)


@pytest.mark.asyncio
async def test_provisioning_failure_returns_generic_error_and_rolls_back(
    bootstrap_harness, monkeypatch
):
    first = await bootstrap_harness.client.post("/api/v1/visitor/bootstrap")
    existing_credential = first.json()["visitor_credential"]
    monkeypatch.setattr(
        "app.services.anonymous_visitor_identity._new_credential",
        lambda: existing_credential,
    )

    response = await bootstrap_harness.client.post("/api/v1/visitor/bootstrap")

    assert response.status_code == 503
    assert response.json()["error"] == "visitor_bootstrap_unavailable"
    assert existing_credential not in response.text
    assert "database" not in response.text.lower()
    assert (await _counts(bootstrap_harness)) == (1, 1, 1)


@pytest.mark.asyncio
async def test_caller_supplied_ownership_fields_are_rejected_without_provisioning(
    bootstrap_harness,
):
    response = await bootstrap_harness.client.post(
        "/api/v1/visitor/bootstrap",
        json={
            "user_id": "attacker-user",
            "visitor_id": "attacker-visitor",
            "namespace": "shared-default",
            "credential_digest": "attacker-digest",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert (await _counts(bootstrap_harness)) == (0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["{malformed", '["not-an-object"]'])
async def test_malformed_bootstrap_body_is_rejected_without_provisioning(
    bootstrap_harness, body
):
    response = await bootstrap_harness.client.post(
        "/api/v1/visitor/bootstrap",
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert (await _counts(bootstrap_harness)) == (0, 0, 0)


@pytest.mark.asyncio
async def test_bootstrap_does_not_change_legacy_pat_authentication(
    bootstrap_harness,
):
    async with bootstrap_harness.factory() as session:
        pat_user, pat = await provision_user(session)
    response = await bootstrap_harness.client.post("/api/v1/visitor/bootstrap")
    async with bootstrap_harness.factory() as session:
        resolved_user = await resolve_bearer_user(session, pat)
        visitor_count = await session.scalar(
            select(func.count(AnonymousVisitorIdentity.id))
        )

    assert response.status_code == 200
    assert resolved_user is not None
    assert resolved_user.id == pat_user.id
    assert visitor_count == 1
