"""Shared API dependencies."""
from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticationDatabaseError, resolve_bearer_session, resolve_bearer_user
from app.core.database import get_db
from app.models.domain import User

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUserSession:
    user: User
    expires_at: datetime


async def get_current_user_session(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthenticatedUserSession:
    """Resolve an active bearer credential with metadata needed by a BFF."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "A valid bearer token is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        resolved = await resolve_bearer_session(db, credentials.credentials)
    except AuthenticationDatabaseError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "authentication_unavailable", "message": str(exc)},
        ) from exc
    if resolved is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "A valid bearer token is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    user, expires_at = resolved
    return AuthenticatedUserSession(user=user, expires_at=expires_at)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the request principal from a valid, active bearer token."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "A valid bearer token is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user = await resolve_bearer_user(db, credentials.credentials)
    except AuthenticationDatabaseError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "authentication_unavailable", "message": str(exc)},
        ) from exc
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "A valid bearer token is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
