"""Authenticated read-only Sui wallet data endpoints."""
import logging
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from app.adapters.tatum.sui_rpc import TatumSuiAdapter, TatumSuiError
from app.api.dependencies import get_current_memory_user
from app.core.config import settings
from app.core.rate_limit import SensitiveEndpointRateLimiter
from app.models.domain import User
from app.services.blockchain_intelligence import analyze_wallet
from app.models.schemas import (
    SuiAddress,
    SuiCursor,
    SuiOwnedObjectsResponse,
    SuiPaginationQuery,
    SuiTransactionResponse,
    SuiWalletBalancesResponse,
    DEFAULT_SUI_PAGE_LIMIT,
    MAX_SUI_PAGE_LIMIT,
)

logger = logging.getLogger("orhex.api.sui")
router = APIRouter(prefix="/sui", tags=["Sui Read-Only"])
_sui_rate_limiter = SensitiveEndpointRateLimiter(
    requests_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE
)
_sui_adapter = TatumSuiAdapter()


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


async def _enforce_rate_limit(request: Request) -> None:
    allowed, retry_after = await _sui_rate_limiter.check(_client_key(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": "Too many Sui requests. Please retry shortly.",
            },
            headers={"Retry-After": str(retry_after)},
        )


def _pagination_query(
    cursor: SuiCursor = None,
    limit: int = Query(
        default=DEFAULT_SUI_PAGE_LIMIT,
        ge=1,
        le=MAX_SUI_PAGE_LIMIT,
    ),
) -> SuiPaginationQuery:
    return SuiPaginationQuery(cursor=cursor, limit=limit)


def _raise_provider_error(exc: TatumSuiError) -> None:
    logger.warning("Tatum Sui request failed: %s", exc.code)
    if exc.code in {"timeout", "connection_error"}:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "provider_unavailable",
                "message": "The Sui data provider is temporarily unavailable.",
            },
        ) from exc
    raise HTTPException(
        status_code=502,
        detail={
            "code": "provider_error",
            "message": "The Sui data provider returned an invalid response.",
        },
    ) from exc


def _validate_provider_response(model, payload):
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        logger.warning("Tatum Sui response failed ORHEX schema validation.")
        raise HTTPException(
            status_code=502,
            detail={
                "code": "invalid_provider_response",
                "message": "The Sui data provider returned an invalid response.",
            },
        ) from exc


def _validate_activity_response(payload):
    try:
        return [
            SuiTransactionResponse.model_validate(item).model_dump()
            for item in payload
        ]
    except (ValidationError, TypeError) as exc:
        logger.warning("Tatum Sui activity failed ORHEX schema validation.")
        raise HTTPException(
            status_code=502,
            detail={
                "code": "invalid_provider_response",
                "message": "The Sui data provider returned an invalid response.",
            },
        ) from exc


def _require_provider_configuration() -> None:
    if not _sui_adapter.api_key:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "provider_unconfigured",
                "message": "Sui data access is not configured.",
            },
        )


@router.get(
    "/wallet/{address}/balances",
    response_model=SuiWalletBalancesResponse,
    responses={401: {}, 422: {}, 502: {}, 503: {}},
)
async def get_wallet_balances(
    request: Request,
    address: SuiAddress,
    pagination: SuiPaginationQuery = Depends(_pagination_query),
    _user: User = Depends(get_current_memory_user),
):
    """Return one bounded page of public coin balances for a Sui address."""
    await _enforce_rate_limit(request)
    _require_provider_configuration()
    try:
        result = await _sui_adapter.get_wallet_balances(
            address,
            limit=pagination.limit,
            cursor=pagination.cursor,
        )
    except TatumSuiError as exc:
        _raise_provider_error(exc)
    return _validate_provider_response(SuiWalletBalancesResponse, result)


@router.get(
    "/wallet/{address}/objects",
    response_model=SuiOwnedObjectsResponse,
    responses={401: {}, 422: {}, 502: {}, 503: {}},
)
async def get_wallet_objects(
    request: Request,
    address: SuiAddress,
    pagination: SuiPaginationQuery = Depends(_pagination_query),
    _user: User = Depends(get_current_memory_user),
):
    """Return one bounded page of public objects owned by a Sui address."""
    await _enforce_rate_limit(request)
    _require_provider_configuration()
    try:
        result = await _sui_adapter.get_owned_objects(
            address,
            limit=pagination.limit,
            cursor=pagination.cursor,
        )
    except TatumSuiError as exc:
        _raise_provider_error(exc)
    return _validate_provider_response(SuiOwnedObjectsResponse, result)


@router.get(
    "/intelligence/{address}",
    responses={401: {}, 422: {}, 502: {}, 503: {}},
)
async def get_wallet_intelligence(
    request: Request,
    address: SuiAddress,
    _user: User = Depends(get_current_memory_user),
):
    """Return an explainable, bounded intelligence snapshot for a public Sui wallet."""
    await _enforce_rate_limit(request)
    _require_provider_configuration()
    try:
        balances, activity, checkpoint = await asyncio.gather(
            _sui_adapter.get_wallet_balances(address, limit=MAX_SUI_PAGE_LIMIT),
            _sui_adapter.get_recent_activity(address, limit=20),
            _sui_adapter.get_latest_checkpoint(),
        )
    except TatumSuiError as exc:
        _raise_provider_error(exc)
    validated = _validate_provider_response(SuiWalletBalancesResponse, balances)
    validated_activity = _validate_activity_response(activity)
    return analyze_wallet(
        address=address, network=validated.network,
        balances=[item.model_dump() for item in validated.balances],
        activity=validated_activity, checkpoint=checkpoint,
    )
