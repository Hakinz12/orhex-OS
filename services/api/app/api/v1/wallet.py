"""Server-side Sui wallet ownership verification endpoints."""
from __future__ import annotations

import base64
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
    WalletAlreadyLinkedError,
    WalletVerificationError,
    WalletVerificationUnavailableError,
    consume_wallet_challenge,
    create_wallet_verification_challenge,
    fetch_wallet_verification_challenge,
)

router = APIRouter(prefix="/wallet", tags=["Sui Wallet Verification"])
_wallet_challenge_limiter = SensitiveEndpointRateLimiter(
    requests_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE,
)
_wallet_verification_limiter = SensitiveEndpointRateLimiter(
    requests_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE,
)


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
    signature: str = Field(max_length=16_384)


class WalletVerificationResponse(BaseModel):
    status: str = "verified"
    chain: str
    network: str
    address: str
    visitor_identity_id: str


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


@router.post("/verify-challenge", response_model=WalletChallengeResponse)
async def issue_wallet_link_challenge(
    request: Request,
    payload: WalletChallengeRequest,
    visitor: AuthenticatedVisitorContext = Depends(get_current_visitor),
    db=Depends(get_db),
):
    """Create a short-lived Sui wallet challenge for the authenticated visitor."""
    await _enforce_rate_limit(request, _wallet_challenge_limiter)
    try:
        challenge, message = await create_wallet_verification_challenge(
            db,
            visitor_identity_id=visitor.identity.id,
            user_id=visitor.user.id,
            purpose=payload.purpose,
            network=payload.network,
            address=payload.address,
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
        raise HTTPException(
            status_code=503,
            detail={
                "code": "wallet_verification_unavailable",
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
    try:
        challenge = await fetch_wallet_verification_challenge(
            db,
            challenge_id=payload.challenge_id,
            visitor_identity_id=visitor.identity.id,
        )
        if challenge is None:
            raise InvalidWalletChallengeError("Wallet verification challenge is invalid.")
        association = await consume_wallet_challenge(
            db,
            challenge_id=payload.challenge_id,
            visitor_identity_id=visitor.identity.id,
            user_id=visitor.user.id,
            signature=payload.signature,
        )
    except WalletVerificationUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "wallet_verification_unavailable",
                "message": "The wallet verification service is temporarily unavailable.",
            },
        ) from exc
    except (InvalidWalletChallengeError, WalletAlreadyLinkedError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "wallet_verification_failed",
                "message": "The wallet verification request is invalid, expired, or already used.",
            },
        ) from exc
    except WalletVerificationError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "wallet_verification_unavailable",
                "message": "The wallet verification service is temporarily unavailable.",
            },
        ) from exc

    return WalletVerificationResponse(
        chain=association.chain,
        network=association.network,
        address=association.canonical_address,
        visitor_identity_id=visitor.identity.id,
    )
