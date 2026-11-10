"""Authenticated read-only Sui wallet data endpoints."""
import logging
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.tatum.sui_rpc import TatumSuiAdapter, TatumSuiError
from app.api.dependencies import AuthenticatedVisitorContext, get_current_visitor
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import SensitiveEndpointRateLimiter
from app.services.blockchain_intelligence import analyze_wallet
from app.services.wallet_verification import (
    normalize_sui_address,
    resolve_verified_visitor_wallet,
)
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
    if exc.code == "authentication_error":
        raise HTTPException(
            status_code=502,
            detail={
                "code": "provider_authentication_failed",
                "message": "The Sui data provider rejected server authentication.",
            },
        ) from exc
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


async def require_verified_wallet_address(
    address: str,
    visitor: AuthenticatedVisitorContext = Depends(get_current_visitor),
    db: AsyncSession = Depends(get_db),
) -> None:
    association = await resolve_verified_visitor_wallet(
        db,
        user_id=visitor.user.id,
        visitor_identity_id=visitor.identity.id,
    )
    if association is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "wallet_not_verified",
                "message": "Connect and verify this Sui wallet before requesting wallet data.",
            },
        )
    if association.canonical_address != normalize_sui_address(address):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "wallet_address_mismatch",
                "message": "Wallet data is available only for the verified Sui account.",
            },
        )


@router.get(
    "/wallet/{address}/balances",
    response_model=SuiWalletBalancesResponse,
    responses={401: {}, 403: {}, 422: {}, 502: {}, 503: {}},
)
async def get_wallet_balances(
    request: Request,
    address: SuiAddress,
    pagination: SuiPaginationQuery = Depends(_pagination_query),
    _verified_wallet: None = Depends(require_verified_wallet_address),
):
    """Return a bounded page of coin balances for the verified visitor wallet."""
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
    responses={401: {}, 403: {}, 422: {}, 502: {}, 503: {}},
)
async def get_wallet_objects(
    request: Request,
    address: SuiAddress,
    pagination: SuiPaginationQuery = Depends(_pagination_query),
    _verified_wallet: None = Depends(require_verified_wallet_address),
):
    """Return a bounded page of objects owned by the verified visitor wallet."""
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
    responses={401: {}, 403: {}, 422: {}, 502: {}, 503: {}},
)
async def get_wallet_intelligence(
    request: Request,
    address: SuiAddress,
    _verified_wallet: None = Depends(require_verified_wallet_address),
):
    """Return an intelligence snapshot only for the verified visitor wallet."""
    await _enforce_rate_limit(request)
    _require_provider_configuration()
    results = await asyncio.gather(
        _sui_adapter.get_wallet_balances(address, limit=MAX_SUI_PAGE_LIMIT),
        _sui_adapter.get_recent_activity(address, limit=20),
        _sui_adapter.get_latest_checkpoint(),
        return_exceptions=True,
    )
    sections = ("balances", "activity", "checkpoint")
    values = dict(zip(sections, results))
    provider_errors = []
    for section, result in values.items():
        if isinstance(result, TatumSuiError):
            logger.warning("Tatum Sui %s read failed: %s", section, result.code)
            code = (
                "authentication_failed"
                if result.code == "authentication_error"
                else "provider_unavailable"
                if result.code in {"timeout", "connection_error"}
                else "provider_error"
            )
            values[section] = None
            provider_errors.append({"section": section, "code": code})
        elif isinstance(result, BaseException):
            raise result

    balances = values["balances"]
    if balances is not None:
        try:
            balances = _validate_provider_response(SuiWalletBalancesResponse, balances)
        except HTTPException as exc:
            if exc.status_code != 502:
                raise
            balances = None
            provider_errors.append({"section": "balances", "code": "invalid_response"})

    activity = values["activity"]
    if activity is not None:
        try:
            activity = _validate_activity_response(activity)
        except HTTPException as exc:
            if exc.status_code != 502:
                raise
            activity = None
            provider_errors.append({"section": "activity", "code": "invalid_response"})

    checkpoint = values["checkpoint"]
    if checkpoint is not None and (
        isinstance(checkpoint, bool) or not isinstance(checkpoint, int) or checkpoint < 0
    ):
        checkpoint = None
        provider_errors.append({"section": "checkpoint", "code": "invalid_response"})

    return analyze_wallet(
        address=address,
        network=(
            balances.network
            if balances is not None
            else _sui_adapter.network
        ),
        balances=(
            [item.model_dump() for item in balances.balances]
            if balances is not None
            else None
        ),
        activity=activity,
        checkpoint=checkpoint,
        provider_errors=provider_errors,
    )
