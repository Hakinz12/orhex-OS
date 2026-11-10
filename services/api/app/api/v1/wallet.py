"""Server-side Sui wallet ownership verification endpoints."""
from __future__ import annotations

import base64
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import AuthenticatedVisitorContext, get_current_visitor
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import SensitiveEndpointRateLimiter
from app.models.schemas import SuiAddress
from app.services.wallet_verification import (
    InvalidWalletChallengeError,
    InvalidWalletSignatureError,
    InvalidWalletSignatureEncodingError,
    WalletAlreadyLinkedError,
    WalletAddressMismatchError,
    WalletChallengeConsumedError,
    WalletChallengeExpiredError,
    WalletChallengeNotFoundError,
    WalletSignatureMissingError,
    WalletSignedMessageMismatchError,
    UnsupportedWalletSignatureSchemeError,
    WalletVerificationError,
    WalletVerificationUnavailableError,
    consume_wallet_challenge,
    create_wallet_verification_challenge,
    fetch_wallet_verification_challenge,
    normalize_wallet_origin,
    revoke_verified_visitor_wallet,
    resolve_verified_visitor_wallet,
)

router = APIRouter(prefix="/wallet", tags=["Sui Wallet Verification"])
logger = logging.getLogger("orhex.api.wallet")
_wallet_challenge_limiter = SensitiveEndpointRateLimiter(
    requests_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE,
)
_wallet_verification_limiter = SensitiveEndpointRateLimiter(
    requests_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE,
)


def _correlation_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) else "unavailable"


class WalletChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = "wallet_link"
    network: str
    address: SuiAddress


class WalletChallengeResponse(BaseModel):
    challenge_id: str
    purpose: str
    chain: str
    network: str
    address: str
    message: str
    expires_at: datetime


class WalletVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str
    signature: str | None = Field(default=None, max_length=16_384)
    signed_message: str | None = Field(default=None, max_length=4_096)


class WalletVerificationResponse(BaseModel):
    status: str = "verified"
    chain: str
    network: str
    address: str
    visitor_identity_id: str


class WalletStatusResponse(BaseModel):
    verified: bool
    chain: str | None = None
    network: str | None = None
    address: str | None = None


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


async def _enforce_rate_limit(request: Request, limiter: SensitiveEndpointRateLimiter) -> None:
    allowed, retry_after = await limiter.check(_client_key(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": "Too many wallet verification requests. Please retry shortly.",
            },
            headers={"Retry-After": str(retry_after)},
        )


def _request_origin(request: Request) -> str:
    try:
        return normalize_wallet_origin(request.headers.get("origin"))
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "invalid_application_origin",
                "message": "Wallet verification must be requested by an allowed ORHEX origin.",
            },
        ) from exc


@router.get("/verified", response_model=WalletStatusResponse)
async def get_verified_wallet(
    visitor: AuthenticatedVisitorContext = Depends(get_current_visitor),
    db=Depends(get_db),
):
    """Return only the wallet verified for the authenticated visitor identity."""
    association = await resolve_verified_visitor_wallet(
        db,
        user_id=visitor.user.id,
        visitor_identity_id=visitor.identity.id,
    )
    if association is None:
        return WalletStatusResponse(verified=False)
    return WalletStatusResponse(
        verified=True,
        chain=association.chain,
        network=association.network,
        address=association.canonical_address,
    )


@router.delete("/verified", response_model=WalletStatusResponse)
async def disconnect_verified_wallet(
    visitor: AuthenticatedVisitorContext = Depends(get_current_visitor),
    db=Depends(get_db),
):
    """Revoke this visitor's active wallet link when the user disconnects."""
    try:
        await revoke_verified_visitor_wallet(
            db,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
        )
    except WalletVerificationError as exc:
        logger.error("Wallet link revocation failed: code=%s", exc.code)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "wallet_verification_unavailable",
                "message": "The wallet verification service is temporarily unavailable.",
            },
        ) from exc
    return WalletStatusResponse(verified=False)


@router.post("/verify-challenge", response_model=WalletChallengeResponse)
async def issue_wallet_link_challenge(
    request: Request,
    payload: WalletChallengeRequest,
    visitor: AuthenticatedVisitorContext = Depends(get_current_visitor),
    db=Depends(get_db),
):
    """Create a short-lived Sui wallet challenge for the authenticated visitor."""
    await _enforce_rate_limit(request, _wallet_challenge_limiter)
    origin = _request_origin(request)
    try:
        challenge, message = await create_wallet_verification_challenge(
            db,
            visitor_identity_id=visitor.identity.id,
            user_id=visitor.user.id,
            purpose=payload.purpose,
            network=payload.network,
            address=payload.address,
            origin=origin,
        )
        logger.info(
            "Wallet verification diagnostic: correlation_id=%s stage=challenge_created result=passed",
            _correlation_id(request),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_wallet_request",
                "message": str(exc),
            },
        ) from exc
    except WalletVerificationError as exc:
        logger.error("Wallet challenge creation failed: code=%s", exc.code)
        raise HTTPException(
            status_code=503,
            detail={
                "code": exc.code,
                "message": "The wallet verification service is temporarily unavailable.",
            },
        ) from exc

    return WalletChallengeResponse(
        challenge_id=challenge.id,
        purpose=challenge.purpose,
        chain=challenge.chain,
        network=challenge.network,
        address=challenge.canonical_address,
        message=base64.b64encode(message).decode("ascii"),
        expires_at=challenge.expires_at,
    )


@router.post("/verify-signature", response_model=WalletVerificationResponse)
async def verify_wallet_signature(
    request: Request,
    payload: WalletVerificationRequest,
    visitor: AuthenticatedVisitorContext = Depends(get_current_visitor),
    db=Depends(get_db),
):
    """Verify a wallet signature and, if valid, create the verified wallet link."""
    await _enforce_rate_limit(request, _wallet_verification_limiter)
    origin = _request_origin(request)
    try:
        challenge = await fetch_wallet_verification_challenge(
            db,
            challenge_id=payload.challenge_id,
            visitor_identity_id=visitor.identity.id,
        )
        if challenge is None:
            raise WalletChallengeNotFoundError("Wallet verification challenge was not found.")
        association = await consume_wallet_challenge(
            db,
            challenge_id=payload.challenge_id,
            visitor_identity_id=visitor.identity.id,
            user_id=visitor.user.id,
            signature=payload.signature,
            signed_message=payload.signed_message,
            origin=origin,
            correlation_id=_correlation_id(request),
        )
    except WalletVerificationUnavailableError as exc:
        logger.error(
            "Wallet verification diagnostic: correlation_id=%s stage=verification_service code=%s",
            _correlation_id(request),
            exc.code,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": exc.code,
                "message": "The wallet verification service is temporarily unavailable.",
            },
        ) from exc
    except (
        WalletChallengeNotFoundError,
        WalletChallengeExpiredError,
        WalletChallengeConsumedError,
        WalletSignedMessageMismatchError,
        WalletAddressMismatchError,
        WalletSignatureMissingError,
        InvalidWalletSignatureError,
        InvalidWalletSignatureEncodingError,
        UnsupportedWalletSignatureSchemeError,
        WalletAlreadyLinkedError,
        InvalidWalletChallengeError,
    ) as exc:
        logger.info(
            "Wallet verification diagnostic: correlation_id=%s stage=verification_rejected code=%s",
            _correlation_id(request),
            exc.code,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": str(exc),
            },
        ) from exc
    except WalletVerificationError as exc:
        logger.error(
            "Wallet verification diagnostic: correlation_id=%s stage=verification_service code=%s",
            _correlation_id(request),
            exc.code,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": exc.code,
                "message": "The wallet verification service is temporarily unavailable.",
            },
        ) from exc
    except Exception as exc:
        logger.exception(
            "Wallet verification diagnostic: correlation_id=%s stage=internal_error type=%s",
            _correlation_id(request),
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "wallet_verification_internal_error",
                "message": "Wallet verification could not be completed due to an internal error.",
            },
        ) from exc

    return WalletVerificationResponse(
        chain=association.chain,
        network=association.network,
        address=association.canonical_address,
        visitor_identity_id=visitor.identity.id,
    )
