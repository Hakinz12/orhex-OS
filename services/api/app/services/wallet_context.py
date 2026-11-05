"""Request-time blockchain context for an authenticated visitor's verified wallet."""
import logging
from decimal import Decimal
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.tatum.sui_rpc import TatumSuiAdapter, TatumSuiError
from app.core.config import settings
from app.models.schemas import (
    MAX_SUI_PAGE_LIMIT,
    SuiCoinBalance,
    SuiWalletBalancesResponse,
)
from app.services.wallet_verification import (
    WalletAssociationError,
    resolve_verified_visitor_wallet,
)

logger = logging.getLogger("orhex.wallet_context")
SUI_COIN_TYPE = "0x2::sui::SUI"


class WalletContext(BaseModel):
    """Ephemeral verified wallet data suitable for downstream context assembly."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["no_wallet", "retrieved", "unavailable"]
    address: str | None = None
    network: str | None = None
    native_balance: SuiCoinBalance | None = None
    token_balances: list[SuiCoinBalance] = Field(default_factory=list)
    has_more_token_balances: bool = False
    error_code: Literal["association_invalid", "provider_unavailable"] | None = None


class _NativeBalanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sui_address: str
    total_balance_sui: str = Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
    raw_balance_mist: str = Field(pattern=r"^(0|[1-9][0-9]*)$")
    network: Literal["mainnet", "testnet", "devnet"]


class _InvalidWalletProviderResponse(ValueError):
    """A Tatum response does not correspond to the verified wallet."""


def _unavailable_association() -> WalletContext:
    return WalletContext(status="unavailable", error_code="association_invalid")


def _unavailable_provider(*, address: str, network: str) -> WalletContext:
    return WalletContext(
        status="unavailable",
        address=address,
        network=network,
        error_code="provider_unavailable",
    )


async def retrieve_visitor_wallet_context(
    session: AsyncSession,
    *,
    user_id: str,
    visitor_identity_id: str,
    adapter: TatumSuiAdapter | None = None,
) -> WalletContext:
    """Fetch bounded current balances for the exact authenticated visitor wallet."""
    try:
        association = await resolve_verified_visitor_wallet(
            session,
            user_id=user_id,
            visitor_identity_id=visitor_identity_id,
        )
    except WalletAssociationError as exc:
        logger.warning("Verified wallet association rejected: %s", type(exc).__name__)
        return _unavailable_association()

    if association is None:
        return WalletContext(status="no_wallet")

    wallet_address = association.canonical_address
    wallet_network = association.network.casefold()
    try:
        tatum = adapter if adapter is not None else TatumSuiAdapter()
        has_api_key = bool(tatum.api_key)
        tatum_network = tatum.network.casefold()
        configured_network = settings.SUI_NETWORK.strip().casefold()
    except (AttributeError, TypeError, ValueError, OSError, httpx.InvalidURL) as exc:
        logger.warning("Tatum wallet adapter configuration failed: %s", type(exc).__name__)
        return _unavailable_provider(address=wallet_address, network=wallet_network)

    if (
        not has_api_key
        or tatum_network != wallet_network
        or tatum_network != configured_network
    ):
        logger.warning("Tatum is not configured for the verified wallet network.")
        return _unavailable_provider(address=wallet_address, network=wallet_network)

    try:
        native_payload = await tatum.get_balance(wallet_address)
        balances_payload = await tatum.get_wallet_balances(
            wallet_address,
            limit=MAX_SUI_PAGE_LIMIT,
        )
        native = _NativeBalanceResponse.model_validate(native_payload)
        balances = SuiWalletBalancesResponse.model_validate(balances_payload)
        if (
            native.sui_address != wallet_address
            or balances.sui_address != wallet_address
            or native.network != wallet_network
            or balances.network != wallet_network
        ):
            raise _InvalidWalletProviderResponse(
                "Tatum returned data for a different address or network."
            )
    except (
        TatumSuiError,
        httpx.TimeoutException,
        httpx.RequestError,
        httpx.InvalidURL,
        TimeoutError,
        ValidationError,
        _InvalidWalletProviderResponse,
    ) as exc:
        logger.warning("Tatum wallet context retrieval failed: %s", type(exc).__name__)
        return _unavailable_provider(address=wallet_address, network=wallet_network)

    native_amount = format(Decimal(native.raw_balance_mist).scaleb(-9), "f")
    if "." in native_amount:
        native_amount = native_amount.rstrip("0").rstrip(".")
    native_balance = SuiCoinBalance(
        coin_type=SUI_COIN_TYPE,
        raw_balance=native.raw_balance_mist,
        amount=native_amount,
        decimals=9,
        symbol="SUI",
    )
    token_balances = [
        balance
        for balance in balances.balances
        if balance.coin_type != SUI_COIN_TYPE
    ]
    if any(not balance.coin_type.strip() for balance in token_balances):
        logger.warning("Tatum wallet context retrieval failed: invalid token type.")
        return _unavailable_provider(address=wallet_address, network=wallet_network)
    return WalletContext(
        status="retrieved",
        address=wallet_address,
        network=wallet_network,
        native_balance=native_balance,
        token_balances=token_balances,
        has_more_token_balances=balances.pagination.has_next_page,
    )
