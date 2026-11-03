"""Lifecycle operations for anonymous visitor identities and credentials."""
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.domain import (
    AnonymousVisitorCredential,
    AnonymousVisitorIdentity,
    User,
    generate_uuid,
)

VISITOR_CREDENTIAL_PREFIX = "orv_"


class VisitorIdentityDatabaseError(RuntimeError):
    """Visitor identity data could not be read or changed reliably."""


class InvalidVisitorCredentialError(ValueError):
    """The supplied visitor credential is invalid, expired, or inactive."""


@dataclass(frozen=True)
class VisitorCredentialIssue:
    identity: AnonymousVisitorIdentity
    user: User
    credential: str


@dataclass(frozen=True)
class VisitorCredentialResolution:
    identity: AnonymousVisitorIdentity
    user: User


def _credential_digest(credential: str) -> str:
    if not isinstance(credential, str) or not credential.startswith(
        VISITOR_CREDENTIAL_PREFIX
    ):
        raise ValueError("Invalid visitor credential.")
    try:
        encoded = credential.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Invalid visitor credential.") from exc
    if len(encoded) < len(VISITOR_CREDENTIAL_PREFIX) + 43:
        raise ValueError("Invalid visitor credential.")
    return hashlib.sha256(encoded).hexdigest()


def _new_credential() -> str:
    return VISITOR_CREDENTIAL_PREFIX + secrets.token_urlsafe(32)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def provision_visitor_identity(
    session: AsyncSession,
) -> VisitorCredentialIssue:
    """Atomically provision a new visitor, its internal user, and first credential."""
    now = _utc_now()
    credential = _new_credential()
    user = User(id=generate_uuid())
    identity = AnonymousVisitorIdentity(
        id=generate_uuid(),
        user_id=user.id,
        created_at=now,
    )
    credential_record = AnonymousVisitorCredential(
        id=generate_uuid(),
        visitor_id=identity.id,
        credential_digest=_credential_digest(credential),
        created_at=now,
    )
    session.add_all([user, identity, credential_record])
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise VisitorIdentityDatabaseError(
            "Could not provision the anonymous visitor identity."
        ) from exc
    return VisitorCredentialIssue(identity, user, credential)


async def reset_visitor_identity(
    session: AsyncSession,
    *,
    credential: str,
    visitor_id: str,
) -> VisitorCredentialIssue:
    """Atomically revoke the current credential and provision a fresh visitor."""
    try:
        digest = _credential_digest(credential)
    except (ValueError, UnicodeEncodeError) as exc:
        raise InvalidVisitorCredentialError(
            "The visitor credential is invalid or inactive."
        ) from exc

    now = _utc_now()
    replacement = _new_credential()
    user = User(id=generate_uuid())
    identity = AnonymousVisitorIdentity(
        id=generate_uuid(),
        user_id=user.id,
        created_at=now,
    )
    credential_record = AnonymousVisitorCredential(
        id=generate_uuid(),
        visitor_id=identity.id,
        credential_digest=_credential_digest(replacement),
        created_at=now,
    )
    try:
        current_result = await session.execute(
            select(AnonymousVisitorCredential).where(
                AnonymousVisitorCredential.visitor_id == visitor_id,
                AnonymousVisitorCredential.credential_digest == digest,
                AnonymousVisitorCredential.revoked_at.is_(None),
            )
        )
        current = current_result.scalar_one_or_none()
        if current is None or not hmac.compare_digest(
            current.credential_digest, digest
        ):
            await session.rollback()
            raise InvalidVisitorCredentialError(
                "The visitor credential is invalid or inactive."
            )
        if current.expires_at is not None:
            expires_at = current.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                await session.rollback()
                raise InvalidVisitorCredentialError(
                    "The visitor credential is invalid or inactive."
                )

        revoked = await session.execute(
            update(AnonymousVisitorCredential)
            .where(
                AnonymousVisitorCredential.visitor_id == visitor_id,
                AnonymousVisitorCredential.credential_digest == digest,
                AnonymousVisitorCredential.revoked_at.is_(None),
                or_(
                    AnonymousVisitorCredential.expires_at.is_(None),
                    AnonymousVisitorCredential.expires_at > now,
                ),
            )
            .values(revoked_at=now)
        )
        if revoked.rowcount != 1:
            await session.rollback()
            raise InvalidVisitorCredentialError(
                "The visitor credential is invalid or inactive."
            )

        session.add_all([user, identity, credential_record])
        await session.commit()
        return VisitorCredentialIssue(identity, user, replacement)
    except InvalidVisitorCredentialError:
        raise
    except SQLAlchemyError as exc:
        await session.rollback()
        raise VisitorIdentityDatabaseError(
            "Could not reset the anonymous visitor identity."
        ) from exc


async def resolve_visitor_credential(
    session: AsyncSession,
    credential: str,
) -> Optional[VisitorCredentialResolution]:
    """Resolve an active visitor credential without accepting PAT credentials."""
    try:
        digest = _credential_digest(credential)
    except (ValueError, UnicodeEncodeError):
        return None

    try:
        result = await session.execute(
            select(AnonymousVisitorCredential)
            .options(
                selectinload(AnonymousVisitorCredential.visitor)
                .selectinload(AnonymousVisitorIdentity.user)
            )
            .where(
                AnonymousVisitorCredential.credential_digest == digest,
                AnonymousVisitorCredential.revoked_at.is_(None),
            )
        )
        record = result.scalar_one_or_none()
        if record is None or not hmac.compare_digest(
            record.credential_digest, digest
        ):
            return None
        if record.expires_at is not None:
            expires_at = record.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= _utc_now():
                return None
        return VisitorCredentialResolution(record.visitor, record.visitor.user)
    except SQLAlchemyError as exc:
        await session.rollback()
        raise VisitorIdentityDatabaseError(
            "Could not verify the visitor credential."
        ) from exc


async def rotate_visitor_credential(
    session: AsyncSession,
    credential: str,
) -> VisitorCredentialIssue:
    """Replace an active credential atomically while retaining its visitor owner."""
    try:
        digest = _credential_digest(credential)
    except (ValueError, UnicodeEncodeError) as exc:
        raise InvalidVisitorCredentialError(
            "The visitor credential is invalid or inactive."
        ) from exc

    now = _utc_now()
    replacement = _new_credential()
    replacement_id = generate_uuid()
    try:
        result = await session.execute(
            select(AnonymousVisitorCredential)
            .options(
                selectinload(AnonymousVisitorCredential.visitor)
                .selectinload(AnonymousVisitorIdentity.user)
            )
            .where(
                AnonymousVisitorCredential.credential_digest == digest,
                AnonymousVisitorCredential.revoked_at.is_(None),
            )
        )
        current = result.scalar_one_or_none()
        if current is None or not hmac.compare_digest(
            current.credential_digest, digest
        ):
            await session.rollback()
            raise InvalidVisitorCredentialError(
                "The visitor credential is invalid or inactive."
            )
        if current.expires_at is not None:
            expires_at = current.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                await session.rollback()
                raise InvalidVisitorCredentialError(
                    "The visitor credential is invalid or inactive."
                )

        updated = await session.execute(
            update(AnonymousVisitorCredential)
            .where(
                AnonymousVisitorCredential.id == current.id,
                AnonymousVisitorCredential.credential_digest == digest,
                AnonymousVisitorCredential.revoked_at.is_(None),
                or_(
                    AnonymousVisitorCredential.expires_at.is_(None),
                    AnonymousVisitorCredential.expires_at > now,
                ),
            )
            .values(
                revoked_at=now,
                replaced_by_credential_id=replacement_id,
            )
        )
        if updated.rowcount != 1:
            await session.rollback()
            raise InvalidVisitorCredentialError(
                "The visitor credential is invalid or inactive."
            )

        replacement_record = AnonymousVisitorCredential(
            id=replacement_id,
            visitor_id=current.visitor_id,
            credential_digest=_credential_digest(replacement),
            created_at=now,
        )
        session.add(replacement_record)
        await session.commit()
        return VisitorCredentialIssue(
            current.visitor,
            current.visitor.user,
            replacement,
        )
    except InvalidVisitorCredentialError:
        raise
    except SQLAlchemyError as exc:
        await session.rollback()
        raise VisitorIdentityDatabaseError(
            "Could not rotate the visitor credential."
        ) from exc


async def revoke_visitor_credential(
    session: AsyncSession,
    credential: str,
) -> bool:
    """Revoke one active visitor credential; invalid credentials are not disclosed."""
    try:
        digest = _credential_digest(credential)
    except (ValueError, UnicodeEncodeError):
        return False

    try:
        result = await session.execute(
            update(AnonymousVisitorCredential)
            .where(
                AnonymousVisitorCredential.credential_digest == digest,
                AnonymousVisitorCredential.revoked_at.is_(None),
            )
            .values(revoked_at=_utc_now())
        )
        if result.rowcount != 1:
            await session.rollback()
            return False
        await session.commit()
        return True
    except SQLAlchemyError as exc:
        await session.rollback()
        raise VisitorIdentityDatabaseError(
            "Could not revoke the visitor credential."
        ) from exc
