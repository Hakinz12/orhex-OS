"""Tests for operator provisioning, deployment configuration, and rate limits."""
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import IntegrityError

from app import cli
from app.core.auth import provision_user, revoke_user_tokens
from app.core.config import Settings, settings
from app.core.database import (
    Base,
    _enable_sqlite_foreign_keys,
    _migrate_user_access_tokens,
)
from app.core.rate_limit import SensitiveEndpointRateLimiter
from app.models.domain import UserAccessToken


def test_legacy_application_name_is_normalized_for_branding():
    legacy = Settings(_env_file=None, APP_NAME="ORHREX OS")
    official = Settings(_env_file=None, APP_NAME="ORHEX OS")

    assert legacy.APP_NAME == "ORHEX OS"
    assert official.APP_NAME == "ORHEX OS"


@pytest.mark.asyncio
async def test_provisioned_tokens_are_random_digested_and_expire_by_configured_ttl(
    monkeypatch,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(settings, "AUTH_TOKEN_TTL_HOURS", 24)

    async with sessions() as session:
        first_user, first_token = await provision_user(session)
        second_user, second_token = await provision_user(session)
        first_record = (
            await session.execute(
                select(UserAccessToken).where(
                    UserAccessToken.user_id == first_user.id
                )
            )
        ).scalar_one()

    assert first_token.startswith("orh_")
    assert second_token.startswith("orh_")
    assert first_token != second_token
    assert first_record.token_digest == hashlib.sha256(
        first_token.encode("ascii")
    ).hexdigest()
    assert first_token not in first_record.token_digest
    assert first_record.expires_at is not None
    lifetime = first_record.expires_at - first_record.created_at
    assert timedelta(hours=23, minutes=59) <= lifetime <= timedelta(hours=24, seconds=1)
    async with sessions() as session:
        assert await revoke_user_tokens(session, user_id=first_user.id) == 1
        revoked_record = (
            await session.execute(
                select(UserAccessToken).where(
                    UserAccessToken.user_id == first_user.id
                )
            )
        ).scalar_one()
        assert revoked_record.revoked_at is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_token_expiration_is_backfilled_and_required():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            "CREATE TABLE user_access_tokens "
            "(id VARCHAR(36) PRIMARY KEY, created_at TIMESTAMP)"
        )
        await connection.exec_driver_sql(
            "INSERT INTO user_access_tokens (id, created_at) "
            "VALUES ('legacy-token', CURRENT_TIMESTAMP)"
        )
        await connection.run_sync(_migrate_user_access_tokens)
        result = await connection.exec_driver_sql(
            "SELECT expires_at FROM user_access_tokens WHERE id='legacy-token'"
        )
        assert result.scalar_one() is not None
        with pytest.raises(IntegrityError):
            await connection.exec_driver_sql(
                "INSERT INTO user_access_tokens (id, created_at, expires_at) "
                "VALUES ('missing-expiry', CURRENT_TIMESTAMP, NULL)"
            )
    await engine.dispose()


def test_provisioning_rejects_non_future_expiration():
    async def run():
        class NoDatabase:
            async def add_all(self, _items):
                raise AssertionError("Database must not be used")

        await provision_user(
            NoDatabase(),
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

    with pytest.raises(ValueError, match="future"):
        asyncio.run(run())


def test_production_https_configuration_is_enforced():
    secure = Settings(
        _env_file=None,
        APP_ENV="production",
        API_PUBLIC_URL="https://api.example.test",
        CORS_ORIGINS="https://app.example.test",
    )
    secure.validate_security_configuration()

    for values in (
        {
            "API_PUBLIC_URL": "http://api.example.test",
            "CORS_ORIGINS": "https://app.example.test",
        },
        {
            "API_PUBLIC_URL": "https://api.example.test",
            "CORS_ORIGINS": "https://app.example.test,*",
        },
        {
            "API_PUBLIC_URL": "",
            "CORS_ORIGINS": "https://app.example.test",
        },
    ):
        config = Settings(_env_file=None, APP_ENV="production", **values)
        with pytest.raises(ValueError):
            config.validate_security_configuration()


@pytest.mark.asyncio
async def test_sensitive_endpoint_rate_limiter_blocks_after_configured_limit():
    limiter = SensitiveEndpointRateLimiter(
        requests_per_minute=2,
        window_seconds=60,
        max_clients=2,
    )
    assert await limiter.check("192.0.2.1", now=100) == (True, 0)
    assert await limiter.check("192.0.2.1", now=110) == (True, 0)
    allowed, retry_after = await limiter.check("192.0.2.1", now=120)
    assert allowed is False
    assert retry_after > 0
    assert await limiter.check("192.0.2.1", now=161) == (True, 0)


@pytest.mark.asyncio
async def test_operator_cli_displays_provisioned_token_once(monkeypatch, capsys):
    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    user = SimpleNamespace(id="user-operator-created")
    token = "orh_one-time-secret-value"
    monkeypatch.setattr(cli, "init_db", AsyncMock())
    monkeypatch.setattr(cli, "AsyncSessionLocal", FakeSession)
    provision = AsyncMock(return_value=(user, token))
    monkeypatch.setattr(cli, "provision_user", provision)

    await cli._provision("Operator Test")

    output = capsys.readouterr().out
    assert output.count(token) == 1
    assert "User ID: user-operator-created" in output
    assert provision.await_args.kwargs["preferred_name"] == "Operator Test"
    assert provision.await_args.kwargs["expires_at"] is not None


@pytest.mark.asyncio
async def test_operator_cli_revokes_users_tokens(monkeypatch, capsys):
    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(cli, "init_db", AsyncMock())
    monkeypatch.setattr(cli, "AsyncSessionLocal", FakeSession)
    revoke = AsyncMock(return_value=2)
    monkeypatch.setattr(cli, "revoke_user_tokens", revoke)

    await cli._revoke("user-to-revoke")

    assert "Revoked 2 active token(s)" in capsys.readouterr().out
    revoke.assert_awaited_once()
    assert revoke.await_args.kwargs["user_id"] == "user-to-revoke"
