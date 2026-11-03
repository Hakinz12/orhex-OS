"""Tests for anonymous visitor identity persistence and credential lifecycle."""
import asyncio
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import IntegrityError

from app.core.auth import (
    provision_user,
    resolve_bearer_session,
    resolve_bearer_user,
)
from app.core.database import (
    Base,
    _enable_sqlite_foreign_keys,
    _migrate_anonymous_visitor_identity,
)
from app.models.domain import (
    AnonymousVisitorCredential,
    AnonymousVisitorIdentity,
    User,
    UserAccessToken,
)
from app.services import anonymous_visitor_identity as visitor_service
from app.services.anonymous_visitor_identity import (
    InvalidVisitorCredentialError,
    VisitorIdentityDatabaseError,
    provision_visitor_identity,
    resolve_visitor_credential,
    revoke_visitor_credential,
    rotate_visitor_credential,
)


async def _make_database(url: str):
    engine = create_async_engine(url, connect_args={"timeout": 10})
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@pytest.fixture
async def visitor_sessions():
    engine, sessions = await _make_database("sqlite+aiosqlite:///:memory:")
    yield sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_provisioning_issues_unique_random_credential_and_stores_only_digest(
    visitor_sessions,
):
    async with visitor_sessions() as session:
        first = await provision_visitor_identity(session)
        second = await provision_visitor_identity(session)
        records = (
            await session.execute(
                select(AnonymousVisitorCredential).order_by(
                    AnonymousVisitorCredential.created_at
                )
            )
        ).scalars().all()

    assert first.credential.startswith("orv_")
    assert second.credential.startswith("orv_")
    assert first.credential != second.credential
    assert len(first.credential.removeprefix("orv_")) == 43
    assert records[0].credential_digest == hashlib.sha256(
        first.credential.encode("ascii")
    ).hexdigest()
    assert records[1].credential_digest == hashlib.sha256(
        second.credential.encode("ascii")
    ).hexdigest()
    assert first.credential not in records[0].credential_digest
    assert all(record.expires_at is None for record in records)
    assert first.identity.id != second.identity.id
    assert first.user.id != second.user.id


@pytest.mark.asyncio
async def test_provisioning_is_atomic_when_credential_insert_fails(
    visitor_sessions, monkeypatch
):
    fixed_credential = "orv_" + ("x" * 43)
    monkeypatch.setattr(visitor_service, "_new_credential", lambda: fixed_credential)

    async with visitor_sessions() as session:
        await provision_visitor_identity(session)
        with pytest.raises(VisitorIdentityDatabaseError):
            await provision_visitor_identity(session)
        assert await session.scalar(select(func.count(User.id))) == 1
        assert await session.scalar(
            select(func.count(AnonymousVisitorIdentity.id))
        ) == 1
        assert await session.scalar(
            select(func.count(AnonymousVisitorCredential.id))
        ) == 1


@pytest.mark.asyncio
async def test_concurrent_provisioning_creates_complete_distinct_identities(tmp_path):
    engine, sessions = await _make_database(
        f"sqlite+aiosqlite:///{Path(tmp_path) / 'concurrent-visitors.db'}"
    )
    try:
        async def provision():
            async with sessions() as session:
                return await provision_visitor_identity(session)

        first, second = await asyncio.gather(provision(), provision())
        async with sessions() as session:
            assert await session.scalar(select(func.count(User.id))) == 2
            assert await session.scalar(
                select(func.count(AnonymousVisitorIdentity.id))
            ) == 2
            assert await session.scalar(
                select(func.count(AnonymousVisitorCredential.id))
            ) == 2
        assert first.identity.user_id != second.identity.user_id
        assert first.identity.id != second.identity.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_credential_resolution_rejects_invalid_and_revoked_credentials(
    visitor_sessions,
):
    async with visitor_sessions() as session:
        issued = await provision_visitor_identity(session)
        resolved = await resolve_visitor_credential(session, issued.credential)
        assert resolved is not None
        assert resolved.identity.id == issued.identity.id
        assert resolved.user.id == issued.user.id
        assert await resolve_visitor_credential(session, "orv_invalid") is None
        assert await resolve_visitor_credential(session, "orh_" + ("a" * 43)) is None

        assert await revoke_visitor_credential(session, issued.credential)
        assert await resolve_visitor_credential(session, issued.credential) is None
        assert not await revoke_visitor_credential(session, issued.credential)
        with pytest.raises(InvalidVisitorCredentialError):
            await rotate_visitor_credential(session, issued.credential)


@pytest.mark.asyncio
async def test_unexpired_and_indefinite_credentials_resolve_at_deterministic_time(
    visitor_sessions, monkeypatch
):
    fixed_now = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(visitor_service, "_utc_now", lambda: fixed_now)

    async with visitor_sessions() as session:
        expiring = await provision_visitor_identity(session)
        indefinite = await provision_visitor_identity(session)
        expiring_record = (
            await session.execute(
                select(AnonymousVisitorCredential).where(
                    AnonymousVisitorCredential.visitor_id == expiring.identity.id
                )
            )
        ).scalar_one()
        expiring_record.expires_at = fixed_now + timedelta(seconds=1)
        await session.commit()

        valid = await resolve_visitor_credential(session, expiring.credential)
        no_expiry = await resolve_visitor_credential(session, indefinite.credential)

    assert valid is not None
    assert valid.identity.id == expiring.identity.id
    assert no_expiry is not None
    assert no_expiry.identity.id == indefinite.identity.id


@pytest.mark.asyncio
async def test_expired_and_exact_boundary_credentials_are_rejected(
    visitor_sessions, monkeypatch
):
    fixed_now = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(visitor_service, "_utc_now", lambda: fixed_now)

    async with visitor_sessions() as session:
        expired = await provision_visitor_identity(session)
        boundary = await provision_visitor_identity(session)
        expired_record = (
            await session.execute(
                select(AnonymousVisitorCredential).where(
                    AnonymousVisitorCredential.visitor_id == expired.identity.id
                )
            )
        ).scalar_one()
        boundary_record = (
            await session.execute(
                select(AnonymousVisitorCredential).where(
                    AnonymousVisitorCredential.visitor_id == boundary.identity.id
                )
            )
        ).scalar_one()
        expired_record.expires_at = fixed_now - timedelta(microseconds=1)
        boundary_record.expires_at = fixed_now
        await session.commit()

        assert await resolve_visitor_credential(session, expired.credential) is None
        assert await resolve_visitor_credential(session, boundary.credential) is None


@pytest.mark.asyncio
async def test_expired_credential_cannot_be_rotated_or_revived(
    visitor_sessions, monkeypatch
):
    fixed_now = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(visitor_service, "_utc_now", lambda: fixed_now)

    async with visitor_sessions() as session:
        issued = await provision_visitor_identity(session)
        identity_id = issued.identity.id
        record = (
            await session.execute(
                select(AnonymousVisitorCredential).where(
                    AnonymousVisitorCredential.visitor_id == identity_id
                )
            )
        ).scalar_one()
        record.expires_at = fixed_now
        await session.commit()

        with pytest.raises(InvalidVisitorCredentialError):
            await rotate_visitor_credential(session, issued.credential)
        records = (
            await session.execute(
                select(AnonymousVisitorCredential).where(
                    AnonymousVisitorCredential.visitor_id == identity_id
                )
            )
        ).scalars().all()

        assert len(records) == 1
        assert _as_utc(records[0].expires_at) == fixed_now
        assert records[0].revoked_at is None
        assert records[0].replaced_by_credential_id is None
        assert await resolve_visitor_credential(session, issued.credential) is None


@pytest.mark.asyncio
async def test_rotation_preserves_identity_and_user_and_rejects_old_credential(
    visitor_sessions,
):
    async with visitor_sessions() as session:
        issued = await provision_visitor_identity(session)
        identity_id = issued.identity.id
        rotated = await rotate_visitor_credential(session, issued.credential)
        old_record = (
            await session.execute(
                select(AnonymousVisitorCredential).where(
                    AnonymousVisitorCredential.credential_digest
                    == hashlib.sha256(issued.credential.encode("ascii")).hexdigest()
                )
            )
        ).scalar_one()

        assert rotated.credential != issued.credential
        assert rotated.identity.id == issued.identity.id
        assert rotated.user.id == issued.user.id
        assert old_record.revoked_at is not None
        assert old_record.replaced_by_credential_id is not None
        assert old_record.replaced_by_credential_id != old_record.id
        assert await resolve_visitor_credential(session, issued.credential) is None
        resolved = await resolve_visitor_credential(session, rotated.credential)
        assert resolved is not None
        assert resolved.identity.id == issued.identity.id
        assert resolved.user.id == issued.user.id
        assert await session.scalar(
            select(func.count(AnonymousVisitorCredential.id)).where(
                AnonymousVisitorCredential.visitor_id == identity_id,
                AnonymousVisitorCredential.revoked_at.is_(None),
            )
        ) == 1


@pytest.mark.asyncio
async def test_rotation_first_allows_revoking_the_replacement_credential(
    visitor_sessions,
):
    async with visitor_sessions() as session:
        issued = await provision_visitor_identity(session)
        identity_id = issued.identity.id
        rotated = await rotate_visitor_credential(session, issued.credential)

        assert await resolve_visitor_credential(session, issued.credential) is None
        assert await resolve_visitor_credential(session, rotated.credential) is not None
        assert not await revoke_visitor_credential(session, issued.credential)
        assert await revoke_visitor_credential(session, rotated.credential)
        assert await resolve_visitor_credential(session, rotated.credential) is None
        assert await session.scalar(
            select(func.count(AnonymousVisitorCredential.id)).where(
                AnonymousVisitorCredential.visitor_id == identity_id,
                AnonymousVisitorCredential.revoked_at.is_(None),
            )
        ) == 0


@pytest.mark.asyncio
async def test_concurrent_rotation_has_at_most_one_winner(tmp_path):
    engine, sessions = await _make_database(
        f"sqlite+aiosqlite:///{Path(tmp_path) / 'concurrent-rotation.db'}"
    )
    try:
        async with sessions() as session:
            issued = await provision_visitor_identity(session)

        async def rotate():
            async with sessions() as session:
                try:
                    return await rotate_visitor_credential(session, issued.credential)
                except (InvalidVisitorCredentialError, VisitorIdentityDatabaseError) as exc:
                    return exc

        outcomes = await asyncio.gather(rotate(), rotate())
        successes = [item for item in outcomes if not isinstance(item, Exception)]
        assert len(successes) == 1
        winner = successes[0]

        async with sessions() as session:
            active_count = await session.scalar(
                select(func.count(AnonymousVisitorCredential.id)).where(
                    AnonymousVisitorCredential.visitor_id == issued.identity.id,
                    AnonymousVisitorCredential.revoked_at.is_(None),
                )
            )
            assert active_count == 1
            assert (
                await resolve_visitor_credential(session, issued.credential)
                is None
            )
            replacement = await resolve_visitor_credential(
                session, winner.credential
            )
            assert replacement is not None
            assert replacement.identity.id == issued.identity.id
            assert replacement.user.id == issued.user.id
            assert await session.scalar(
                select(func.count(AnonymousVisitorIdentity.id))
            ) == 1
            assert await session.scalar(
                select(func.count(AnonymousVisitorCredential.id)).where(
                    AnonymousVisitorCredential.visitor_id == issued.identity.id
                )
            ) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_rotation_and_revocation_leave_consistent_credentials(
    tmp_path,
):
    engine, sessions = await _make_database(
        f"sqlite+aiosqlite:///{Path(tmp_path) / 'rotation-revocation-race.db'}"
    )
    try:
        async with sessions() as session:
            issued = await provision_visitor_identity(session)
            identity_id = issued.identity.id

        start = asyncio.Barrier(2)

        async def rotate():
            async with sessions() as session:
                await start.wait()
                try:
                    return await rotate_visitor_credential(
                        session, issued.credential
                    )
                except (InvalidVisitorCredentialError, VisitorIdentityDatabaseError) as exc:
                    return exc

        async def revoke():
            async with sessions() as session:
                await start.wait()
                try:
                    return await revoke_visitor_credential(
                        session, issued.credential
                    )
                except VisitorIdentityDatabaseError as exc:
                    return exc

        rotation_result, revocation_result = await asyncio.gather(rotate(), revoke())

        async with sessions() as session:
            active_records = (
                await session.execute(
                    select(AnonymousVisitorCredential).where(
                        AnonymousVisitorCredential.visitor_id == identity_id,
                        AnonymousVisitorCredential.revoked_at.is_(None),
                    )
                )
            ).scalars().all()
            old_resolution = await resolve_visitor_credential(
                session, issued.credential
            )

            assert len(active_records) <= 1
            assert old_resolution is None or (
                len(active_records) == 1
                and active_records[0].credential_digest
                == hashlib.sha256(issued.credential.encode("ascii")).hexdigest()
                and not isinstance(rotation_result, visitor_service.VisitorCredentialIssue)
                and revocation_result is not True
            )

            if isinstance(rotation_result, visitor_service.VisitorCredentialIssue):
                assert old_resolution is None
                assert len(active_records) == 1
                assert revocation_result is False or isinstance(
                    revocation_result, VisitorIdentityDatabaseError
                )
                assert await revoke_visitor_credential(
                    session, rotation_result.credential
                )
            elif revocation_result is True:
                assert old_resolution is None
                assert active_records == []
                assert isinstance(
                    rotation_result,
                    (InvalidVisitorCredentialError, VisitorIdentityDatabaseError),
                )
            else:
                assert isinstance(rotation_result, Exception)
                assert isinstance(revocation_result, Exception)
                assert old_resolution is not None
                assert len(active_records) == 1
                assert await revoke_visitor_credential(
                    session, issued.credential
                )

            assert await session.scalar(
                select(func.count(AnonymousVisitorCredential.id)).where(
                    AnonymousVisitorCredential.visitor_id == identity_id,
                    AnonymousVisitorCredential.revoked_at.is_(None),
                )
            ) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_visitor_credentials_do_not_fall_back_to_pat_auth_and_pat_still_works(
    visitor_sessions, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_TOKEN_TTL_HOURS", 24)
    async with visitor_sessions() as session:
        visitor = await provision_visitor_identity(session)
        pat_user, pat = await provision_user(session)

        assert await resolve_bearer_user(session, visitor.credential) is None
        assert await resolve_visitor_credential(session, pat) is None
        assert await resolve_bearer_user(session, pat) == pat_user
        visitor_resolution = await resolve_visitor_credential(
            session, visitor.credential
        )
        assert visitor_resolution is not None
        assert visitor_resolution.user.id == visitor.user.id
        assert visitor_resolution.user.id != pat_user.id


@pytest.mark.asyncio
async def test_visitor_migration_is_additive_and_idempotent():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                "CREATE TABLE users (id VARCHAR(64) PRIMARY KEY)"
            )
            await connection.exec_driver_sql(
                "CREATE TABLE user_access_tokens "
                "(id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64), "
                "token_digest VARCHAR(64))"
            )
            await connection.exec_driver_sql(
                "CREATE TABLE memory_sync_records "
                "(id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64), "
                "walrus_blob_id VARCHAR(256), namespace VARCHAR(128))"
            )
            await connection.exec_driver_sql(
                "INSERT INTO users (id) VALUES ('legacy-user')"
            )
            await connection.exec_driver_sql(
                "INSERT INTO user_access_tokens "
                "(id, user_id, token_digest) "
                "VALUES ('legacy-token', 'legacy-user', 'legacy-digest')"
            )
            await connection.exec_driver_sql(
                "INSERT INTO memory_sync_records "
                "(id, user_id, walrus_blob_id, namespace) "
                "VALUES ('legacy-memory', 'legacy-user', 'walrus-legacy', "
                "'legacy-namespace')"
            )
            await connection.run_sync(_migrate_anonymous_visitor_identity)
            await connection.run_sync(_migrate_anonymous_visitor_identity)

            assert (
                await connection.exec_driver_sql(
                    "SELECT user_id, token_digest FROM user_access_tokens "
                    "WHERE id='legacy-token'"
                )
            ).one() == ("legacy-user", "legacy-digest")
            assert (
                await connection.exec_driver_sql(
                    "SELECT user_id, walrus_blob_id, namespace "
                    "FROM memory_sync_records WHERE id='legacy-memory'"
                )
            ).one() == ("legacy-user", "walrus-legacy", "legacy-namespace")
            names = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            assert "anonymous_visitor_identities" in names
            assert "anonymous_visitor_credentials" in names
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_visitor_migration_preserves_and_resolves_legacy_pat(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{Path(tmp_path) / 'legacy-pat-migration.db'}",
        connect_args={"timeout": 10},
    )
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    sessions = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(User.__table__.create)
            await connection.run_sync(UserAccessToken.__table__.create)

        async with sessions() as session:
            original_user, pat = await provision_user(session)
            original_digest = hashlib.sha256(pat.encode("ascii")).hexdigest()
            original_record = (
                await session.execute(
                    select(UserAccessToken).where(
                        UserAccessToken.token_digest == original_digest
                    )
                )
            ).scalar_one()
            original_expiry = _as_utc(original_record.expires_at)
            original_revocation = original_record.revoked_at

        async with engine.begin() as connection:
            await connection.run_sync(_migrate_anonymous_visitor_identity)
            await connection.run_sync(_migrate_anonymous_visitor_identity)

        async with sessions() as session:
            resolved = await resolve_bearer_session(session, pat)
            migrated_record = (
                await session.execute(
                    select(UserAccessToken).where(
                        UserAccessToken.token_digest == original_digest
                    )
                )
            ).scalar_one()
            visitor_count = await session.scalar(
                select(func.count(AnonymousVisitorIdentity.id))
            )
        async with engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )

        assert resolved is not None
        assert resolved[0].id == original_user.id
        assert _as_utc(resolved[1]) == original_expiry
        assert migrated_record.user_id == original_user.id
        assert migrated_record.token_digest == original_digest
        assert _as_utc(migrated_record.expires_at) == original_expiry
        assert migrated_record.revoked_at == original_revocation
        assert original_revocation is None
        assert visitor_count == 0
        assert "anonymous_visitor_identities" in table_names
        assert "anonymous_visitor_credentials" in table_names
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_visitor_identity_and_credential_foreign_keys_are_enforced(
    visitor_sessions,
):
    async with visitor_sessions() as session:
        session.add(
            AnonymousVisitorIdentity(
                id="missing-user-identity",
                user_id="missing-user",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

        user = User(id="visitor-owner")
        identity = AnonymousVisitorIdentity(id="visitor-identity", user=user)
        session.add_all([user, identity])
        await session.flush()
        session.add(
            AnonymousVisitorCredential(
                id="orphaned-credential",
                visitor_id="missing-visitor",
                credential_digest="a" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
