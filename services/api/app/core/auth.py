"""Out-of-band provisioning and verification for opaque user bearer tokens."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.domain import User, UserAccessToken, generate_uuid

TOKEN_PREFIX = "orh_"


class AuthenticationDatabaseError(RuntimeError):
    """Authentication data could not be read reliably."""


def _token_digest(token: str) -> str:
    if not token.startswith(TOKEN_PREFIX) or len(token) < 40:
        raise ValueError("Invalid bearer token.")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


async def provision_user(
    session: AsyncSession,
    *,
    preferred_name: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> Tuple[User, str]:
    """Create a user and one-time-return API token in a trusted admin workflow."""
    now = datetime.now(timezone.utc)
    expires_at = expires_at or (
        now + timedelta(hours=settings.AUTH_TOKEN_TTL_HOURS)
    )
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        raise ValueError("Token expiration must be in the future.")

    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    user = User(id=generate_uuid(), preferred_name=preferred_name)
    credential = UserAccessToken(
        id=generate_uuid(),
        user_id=user.id,
        token_digest=_token_digest(token),
        created_at=now,
        expires_at=expires_at,
    )
    session.add_all([user, credential])
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise AuthenticationDatabaseError(
            "Could not provision the application user."
        ) from exc
    return user, token


async def revoke_user_token(
    session: AsyncSession, *, user_id: str, token_id: str
) -> bool:
    """Revoke one credential for an owned user."""
    try:
        result = await session.execute(
            select(UserAccessToken).where(
                UserAccessToken.id == token_id,
                UserAccessToken.user_id == user_id,
                UserAccessToken.revoked_at.is_(None),
            )
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            await session.rollback()
            return False
        credential.revoked_at = datetime.now(timezone.utc)
        await session.commit()
        return True
    except SQLAlchemyError as exc:
        await session.rollback()
        raise AuthenticationDatabaseError(
            "Could not revoke the application token."
        ) from exc


async def revoke_user_tokens(session: AsyncSession, *, user_id: str) -> int:
    """Revoke every active access token for one user in a single transaction."""
    try:
        result = await session.execute(
            update(UserAccessToken)
            .where(
                UserAccessToken.user_id == user_id,
                UserAccessToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await session.commit()
        return result.rowcount or 0
    except SQLAlchemyError as exc:
        await session.rollback()
        raise AuthenticationDatabaseError(
            "Could not revoke the application user's tokens."
        ) from exc


async def resolve_bearer_user(
    session: AsyncSession, token: str
) -> Optional[User]:
    """Resolve an active bearer credential to its verified application user."""
    try:
        digest = _token_digest(token)
    except (ValueError, UnicodeEncodeError):
        return None

    try:
        result = await session.execute(
            select(UserAccessToken)
            .options(selectinload(UserAccessToken.user))
            .where(
                UserAccessToken.token_digest == digest,
                UserAccessToken.revoked_at.is_(None),
            )
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            return None
        if credential.expires_at is None:
            return None
        expiration = credential.expires_at
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=timezone.utc)
        if expiration <= datetime.now(timezone.utc):
            return None
        return credential.user
    except SQLAlchemyError as exc:
        await session.rollback()
        raise AuthenticationDatabaseError(
            "Could not verify the bearer credential."
        ) from exc
