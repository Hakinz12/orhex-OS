"""Anonymous visitor bootstrap and session-management endpoints."""
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import AuthenticatedVisitorContext, get_current_visitor
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import SensitiveEndpointRateLimiter
from app.models.domain import AnonymousVisitorCredential
from app.services.anonymous_visitor_identity import (
    InvalidVisitorCredentialError,
    VisitorIdentityDatabaseError,
    provision_visitor_identity,
    revoke_visitor_credential,
    reset_visitor_identity,
    rotate_visitor_credential,
)

router = APIRouter(prefix="/visitor", tags=["Anonymous Visitors"])
_bootstrap_rate_limiter = SensitiveEndpointRateLimiter(
    requests_per_minute=settings.VISITOR_BOOTSTRAP_RATE_LIMIT_PER_MINUTE
)
_session_rate_limiter = SensitiveEndpointRateLimiter(
    requests_per_minute=settings.VISITOR_BOOTSTRAP_RATE_LIMIT_PER_MINUTE
)
_visitor_bearer_scheme = HTTPBearer(auto_error=False)


class VisitorBootstrapRequest(BaseModel):
    """Bootstrap accepts no caller-supplied identity or ownership values."""

    model_config = ConfigDict(extra="forbid")


class VisitorBootstrapResponse(BaseModel):
    """One-time credential response for a trusted server-side BFF."""

    visitor_credential: str


class VisitorSessionResponse(BaseModel):
    """Session metadata safe for authenticated visitor inspection."""

    id: str
    user_id: str
    created_at: datetime
    credential_created_at: datetime
    credential_expires_at: datetime | None = None
    status: str = "active"


class VisitorResetResponse(BaseModel):
    """New isolated identity and one-time credential for a trusted BFF."""

    visitor_identity_id: str
    visitor_credential: str


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _bearer_from_request(request: Request) -> str:
    authorization = request.headers.get("authorization")
    if authorization is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid visitor credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid visitor credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return value.strip()


async def _session_metadata_for_visitor(
    db: AsyncSession,
    visitor: AuthenticatedVisitorContext,
) -> VisitorSessionResponse:
    result = await db.execute(
        select(AnonymousVisitorCredential)
        .where(
            AnonymousVisitorCredential.visitor_id == visitor.identity.id,
            AnonymousVisitorCredential.revoked_at.is_(None),
        )
        .order_by(AnonymousVisitorCredential.created_at.desc())
    )
    credential = result.scalar_one_or_none()
    if credential is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid visitor credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return VisitorSessionResponse(
        id=visitor.identity.id,
        user_id=visitor.identity.user_id,
        created_at=visitor.identity.created_at,
        credential_created_at=credential.created_at,
        credential_expires_at=credential.expires_at,
        status="active",
    )


@router.post("/bootstrap", response_model=VisitorBootstrapResponse)
async def bootstrap_visitor(
    request: Request,
    _payload: VisitorBootstrapRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
) -> VisitorBootstrapResponse:
    """Provision one isolated visitor identity after applying a direct-IP limit."""
    peer = _client_key(request)
    allowed, retry_after = await _bootstrap_rate_limiter.check(peer)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": "Too many visitor bootstrap requests. Try again later.",
            },
            headers={"Retry-After": str(retry_after)},
        )

    try:
        issued = await provision_visitor_identity(db)
    except VisitorIdentityDatabaseError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "visitor_bootstrap_unavailable",
                "message": "Visitor identity could not be created.",
            },
        ) from exc

    return VisitorBootstrapResponse(visitor_credential=issued.credential)


@router.get("/session", response_model=VisitorSessionResponse)
async def get_visitor_session(
    request: Request,
    visitor: AuthenticatedVisitorContext = Depends(get_current_visitor),
    db: AsyncSession = Depends(get_db),
) -> VisitorSessionResponse:
    """Return only safe visitor session metadata for the authenticated identity."""
    del request
    return await _session_metadata_for_visitor(db, visitor)


@router.post("/session/rotate", response_model=VisitorBootstrapResponse)
async def rotate_visitor_session(
    request: Request,
    visitor: AuthenticatedVisitorContext = Depends(get_current_visitor),
    db: AsyncSession = Depends(get_db),
) -> VisitorBootstrapResponse:
    """Rotate the current credential while keeping the underlying visitor identity."""
    peer = _client_key(request)
    allowed, retry_after = await _session_rate_limiter.check(peer)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": "Too many visitor session updates. Try again later.",
            },
            headers={"Retry-After": str(retry_after)},
        )

    credential = _bearer_from_request(request)
    try:
        rotated = await rotate_visitor_credential(db, credential)
    except InvalidVisitorCredentialError as exc:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid visitor credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except VisitorIdentityDatabaseError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "visitor_session_unavailable",
                "message": "Visitor session could not be updated.",
            },
        ) from exc

    if rotated.identity.id != visitor.identity.id:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid visitor credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    return VisitorBootstrapResponse(visitor_credential=rotated.credential)


@router.delete("/session")
async def revoke_visitor_session(
    request: Request,
    visitor: AuthenticatedVisitorContext = Depends(get_current_visitor),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Revoke only the currently authenticated visitor credential."""
    peer = _client_key(request)
    allowed, retry_after = await _session_rate_limiter.check(peer)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": "Too many visitor session updates. Try again later.",
            },
            headers={"Retry-After": str(retry_after)},
        )

    credential = _bearer_from_request(request)
    try:
        revoked = await revoke_visitor_credential(db, credential)
    except VisitorIdentityDatabaseError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "visitor_session_unavailable",
                "message": "Visitor session could not be updated.",
            },
        ) from exc
    if not revoked:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid visitor credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    if visitor.identity.id is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid visitor credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"status": "revoked"}


@router.post("/session/reset", response_model=VisitorResetResponse)
async def reset_visitor_session(
    request: Request,
    _payload: VisitorBootstrapRequest | None = Body(default=None),
    visitor: AuthenticatedVisitorContext = Depends(get_current_visitor),
    db: AsyncSession = Depends(get_db),
) -> VisitorResetResponse:
    """Revoke this credential and atomically start a fresh isolated identity."""
    allowed, retry_after = await _session_rate_limiter.check(_client_key(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": "Too many visitor session updates. Try again later.",
            },
            headers={"Retry-After": str(retry_after)},
        )

    authorization = request.headers.get("authorization")
    if authorization is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid visitor credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid visitor credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        reset = await reset_visitor_identity(
            db,
            credential=credential.strip(),
            visitor_id=visitor.identity.id,
        )
    except InvalidVisitorCredentialError as exc:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "A valid visitor credential is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except VisitorIdentityDatabaseError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "visitor_reset_unavailable",
                "message": "Visitor identity could not be reset.",
            },
        ) from exc

    return VisitorResetResponse(
        visitor_identity_id=reset.identity.id,
        visitor_credential=reset.credential,
    )
