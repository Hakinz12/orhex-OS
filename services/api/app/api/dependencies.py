"""Shared API dependencies."""
from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    TOKEN_PREFIX,
    AuthenticationDatabaseError,
    resolve_bearer_session,
    resolve_bearer_user,
)
from app.core.database import get_db
from app.models.domain import AnonymousVisitorIdentity, User
from app.services.anonymous_visitor_identity import (
    VISITOR_CREDENTIAL_PREFIX,
    VisitorIdentityDatabaseError,
    resolve_visitor_credential,
)

_bearer_scheme = HTTPBearer(auto_error=False)
_visitor_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUserSession:
    user: User
    expires_at: datetime


@dataclass(frozen=True)
class AuthenticatedVisitorIdentity:
    """Credential-free identity fields for an authenticated visitor."""

    id: str
    user_id: str
    created_at: datetime


@dataclass(frozen=True)
class AuthenticatedVisitorContext:
    """Verified visitor identity and internal owner without credential data."""

    identity: AuthenticatedVisitorIdentity
    user: User


@dataclass(frozen=True)
class AuthenticatedConversationOwner:
    """Conversation ownership derived from exactly one authenticated credential."""

    user_id: str
    visitor_identity_id: str | None


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


async def get_current_visitor(
    credentials: HTTPAuthorizationCredentials = Depends(_visitor_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthenticatedVisitorContext:
    """Resolve only visitor credentials; PAT credentials are never considered."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid visitor credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        resolved = await resolve_visitor_credential(db, credentials.credentials)
    except VisitorIdentityDatabaseError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "authentication_unavailable",
                "message": "Visitor authentication is temporarily unavailable.",
            },
        ) from exc
    if resolved is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid visitor credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AuthenticatedVisitorContext(
        identity=AuthenticatedVisitorIdentity(
            id=resolved.identity.id,
            user_id=resolved.identity.user_id,
            created_at=resolved.identity.created_at,
        ),
        user=resolved.user,
    )


async def get_current_conversation_owner(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthenticatedConversationOwner:
    """Resolve visitor and PAT credentials into distinct conversation scopes."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "A valid bearer credential is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        if token.startswith(VISITOR_CREDENTIAL_PREFIX):
            visitor = await resolve_visitor_credential(db, token)
            if visitor is not None:
                return AuthenticatedConversationOwner(
                    user_id=visitor.user.id,
                    visitor_identity_id=visitor.identity.id,
                )
        elif token.startswith(TOKEN_PREFIX):
            user = await resolve_bearer_user(db, token)
            if user is not None:
                return AuthenticatedConversationOwner(
                    user_id=user.id,
                    visitor_identity_id=None,
                )
    except (AuthenticationDatabaseError, VisitorIdentityDatabaseError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "authentication_unavailable",
                "message": "Authentication is temporarily unavailable.",
            },
        ) from exc

    raise HTTPException(
        status_code=401,
        detail={"code": "unauthenticated", "message": "A valid bearer credential is required."},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_memory_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve a PAT or visitor credential for the shared owner-scoped memory API."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid bearer credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        if token.startswith(TOKEN_PREFIX):
            user = await resolve_bearer_user(db, token)
        elif token.startswith(VISITOR_CREDENTIAL_PREFIX):
            visitor = await resolve_visitor_credential(db, token)
            user = visitor.user if visitor is not None else None
        else:
            user = None
    except (AuthenticationDatabaseError, VisitorIdentityDatabaseError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "authentication_unavailable",
                "message": "Authentication is temporarily unavailable.",
            },
        ) from exc

    if user is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid bearer credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
