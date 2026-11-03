"""HTTP integration tests for the separate anonymous visitor auth dependency."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import dependencies
from app.core.auth import provision_user
from app.core.database import Base, _enable_sqlite_foreign_keys, get_db
from app.main import handle_http_error
from app.models.domain import AnonymousVisitorCredential, User
from app.services import anonymous_visitor_identity as visitor_service
from app.services.anonymous_visitor_identity import (
    VisitorIdentityDatabaseError,
    provision_visitor_identity,
    revoke_visitor_credential,
)


@pytest.fixture
async def auth_harness():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_db():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[get_db] = override_db
    app.add_exception_handler(HTTPException, handle_http_error)

    @app.get("/visitor")
    async def visitor_endpoint(
        visitor: dependencies.AuthenticatedVisitorContext = Depends(
            dependencies.get_current_visitor
        ),
        user_id: str | None = None,
        visitor_id: str | None = None,
        namespace: str | None = None,
    ):
        return {
            "identity_id": visitor.identity.id,
            "identity_user_id": visitor.identity.user_id,
            "user_id": visitor.user.id,
            "claims": [user_id, visitor_id, namespace],
        }

    @app.get("/pat")
    async def pat_endpoint(user: User = Depends(dependencies.get_current_user)):
        return {"user_id": user.id}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield SimpleNamespace(
                client=client,
                factory=session_factory,
                app=app,
            )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def _provision_visitor(harness):
    async with harness.factory() as session:
        issue = await provision_visitor_identity(session)
    return issue


@pytest.mark.asyncio
async def test_valid_visitor_credential_authenticates_safe_context(auth_harness):
    issue = await _provision_visitor(auth_harness)
    response = await auth_harness.client.get(
        "/visitor",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "identity_id": issue.identity.id,
        "identity_user_id": issue.user.id,
        "user_id": issue.user.id,
        "claims": [None, None, None],
    }

    async with auth_harness.factory() as session:
        record = (
            await session.execute(
                select(AnonymousVisitorCredential).where(
                    AnonymousVisitorCredential.visitor_id == issue.identity.id
                )
            )
        ).scalar_one()
    assert issue.credential not in record.credential_digest
    assert record.credential_digest not in response.text


@pytest.mark.asyncio
async def test_context_contains_no_credential_or_digest_fields(auth_harness):
    issue = await _provision_visitor(auth_harness)
    async with auth_harness.factory() as session:
        context = await dependencies.get_current_visitor(
            credentials=dependencies.HTTPAuthorizationCredentials(
                scheme="Bearer",
                credentials=issue.credential,
            ),
            db=session,
        )

    assert set(context.__dataclass_fields__) == {"identity", "user"}
    assert set(context.identity.__dataclass_fields__) == {
        "id",
        "user_id",
        "created_at",
    }
    assert issue.credential not in repr(context)
    assert "credential_digest" not in repr(context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        None,
        {"Authorization": "Basic not-a-bearer"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer orv_"},
        {"Authorization": "Bearer orv_" + ("z" * 43)},
        {"Authorization": "Bearer orh_" + ("z" * 43)},
    ],
    ids=[
        "missing",
        "wrong-scheme",
        "missing-token",
        "empty-token",
        "malformed-visitor",
        "unknown-visitor",
        "pat-in-visitor-path",
    ],
)
async def test_invalid_visitor_authorization_fails_closed(auth_harness, headers):
    response = await auth_harness.client.get("/visitor", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"] == "unauthenticated"
    assert "orv_" not in response.text
    assert "orh_" not in response.text
    assert "digest" not in response.text
    assert "database" not in response.text.lower()


@pytest.mark.asyncio
async def test_expired_visitor_credential_returns_401(auth_harness, monkeypatch):
    fixed_now = datetime(2030, 4, 5, 6, 7, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(visitor_service, "_utc_now", lambda: fixed_now)
    issue = await _provision_visitor(auth_harness)

    async with auth_harness.factory() as session:
        record = (
            await session.execute(
                select(AnonymousVisitorCredential).where(
                    AnonymousVisitorCredential.visitor_id == issue.identity.id
                )
            )
        ).scalar_one()
        record.expires_at = fixed_now - timedelta(microseconds=1)
        await session.commit()

    response = await auth_harness.client.get(
        "/visitor",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"
    assert issue.credential not in response.text


@pytest.mark.asyncio
async def test_revoked_visitor_credential_returns_401(auth_harness):
    issue = await _provision_visitor(auth_harness)
    async with auth_harness.factory() as session:
        assert await revoke_visitor_credential(session, issue.credential)

    response = await auth_harness.client.get(
        "/visitor",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"


@pytest.mark.asyncio
async def test_visitor_credential_does_not_authenticate_through_pat_dependency(
    auth_harness,
):
    issue = await _provision_visitor(auth_harness)
    response = await auth_harness.client.get(
        "/pat",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"


@pytest.mark.asyncio
async def test_pat_does_not_authenticate_through_visitor_dependency(auth_harness):
    async with auth_harness.factory() as session:
        user, pat = await provision_user(session)

    visitor_response = await auth_harness.client.get(
        "/visitor",
        headers={"Authorization": f"Bearer {pat}"},
    )
    pat_response = await auth_harness.client.get(
        "/pat",
        headers={"Authorization": f"Bearer {pat}"},
    )

    assert visitor_response.status_code == 401
    assert visitor_response.json()["error"] == "unauthenticated"
    assert pat_response.status_code == 200
    assert pat_response.json() == {"user_id": user.id}


@pytest.mark.asyncio
async def test_two_visitors_have_distinct_authenticated_ownership(auth_harness):
    first = await _provision_visitor(auth_harness)
    second = await _provision_visitor(auth_harness)

    first_response, second_response = await asyncio.gather(
        auth_harness.client.get(
            "/visitor",
            headers={"Authorization": f"Bearer {first.credential}"},
        ),
        auth_harness.client.get(
            "/visitor",
            headers={"Authorization": f"Bearer {second.credential}"},
        ),
    )

    assert first_response.status_code == second_response.status_code == 200
    assert first_response.json()["identity_id"] == first.identity.id
    assert first_response.json()["user_id"] == first.user.id
    assert second_response.json()["identity_id"] == second.identity.id
    assert second_response.json()["user_id"] == second.user.id
    assert first.identity.id != second.identity.id
    assert first.user.id != second.user.id


@pytest.mark.asyncio
async def test_client_ownership_claims_do_not_override_visitor_context(auth_harness):
    issue = await _provision_visitor(auth_harness)
    response = await auth_harness.client.get(
        "/visitor",
        params={
            "user_id": "attacker-user",
            "visitor_id": "attacker-visitor",
            "namespace": "shared-default",
        },
        headers={"Authorization": f"Bearer {issue.credential}"},
    )

    assert response.status_code == 200
    assert response.json()["identity_id"] == issue.identity.id
    assert response.json()["identity_user_id"] == issue.user.id
    assert response.json()["user_id"] == issue.user.id
    assert response.json()["claims"] == [
        "attacker-user",
        "attacker-visitor",
        "shared-default",
    ]


@pytest.mark.asyncio
async def test_visitor_database_failure_is_generic_and_fails_closed(
    auth_harness, monkeypatch
):
    async def fail_resolution(_session, _credential):
        raise VisitorIdentityDatabaseError(
            "database-secret-detail visitor credential digest"
        )

    monkeypatch.setattr(dependencies, "resolve_visitor_credential", fail_resolution)
    response = await auth_harness.client.get(
        "/visitor",
        headers={"Authorization": "Bearer orv_" + ("a" * 43)},
    )

    assert response.status_code == 503
    assert response.json()["error"] == "authentication_unavailable"
    assert "database-secret-detail" not in response.text
    assert "digest" not in response.text
