"""Tatum Sui GraphQL adapter for read-only blockchain intelligence."""
import logging
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any, Dict, List, Optional

import httpx
from pydantic import ValidationError

from app.adapters.tatum.base import BaseBlockchainAdapter
from app.core.config import settings
from app.models.schemas import (
    SuiPaginationMetadata,
    SuiPaginationQuery,
    SuiTransactionResponse,
)

logger = logging.getLogger(__name__)

_SUI_CHAIN_IDENTIFIERS = {
    "mainnet": "4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S",
    "testnet": "69WiPg3DAQiwdxfncX6wYQ2siKwAe6L9BZthQea3JNMD",
}


class TatumSuiError(RuntimeError):
    """A safe, non-sensitive Tatum Sui request or response error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _decimal_amount(raw_amount: str, decimals: int) -> str:
    with localcontext() as context:
        context.prec = max(28, len(raw_amount) + decimals + 2)
        amount = format(Decimal(raw_amount).scaleb(-decimals), "f")
    if "." in amount:
        amount = amount.rstrip("0").rstrip(".")
    return amount or "0"


def _pagination_values(limit: int, cursor: Optional[str]) -> tuple[int, Optional[str]]:
    try:
        pagination = SuiPaginationQuery(limit=limit, cursor=cursor)
    except ValueError as exc:
        raise ValueError("Sui pagination parameters are invalid.") from exc
    return pagination.limit, pagination.cursor


def _pagination_metadata(page_info: Dict[str, Any], resource: str) -> SuiPaginationMetadata:
    has_next_page = page_info.get("hasNextPage")
    end_cursor = page_info.get("endCursor")
    if not isinstance(has_next_page, bool):
        raise TatumSuiError(
            "invalid_response",
            f"Tatum Sui returned invalid {resource} pagination.",
        )
    if has_next_page and not isinstance(end_cursor, str):
        raise TatumSuiError(
            "invalid_response",
            f"Tatum Sui omitted the next {resource} cursor.",
        )
    try:
        return SuiPaginationMetadata(
            next_cursor=end_cursor if has_next_page else None,
            has_next_page=has_next_page,
        )
    except ValidationError as exc:
        raise TatumSuiError(
            "invalid_response",
            f"Tatum Sui returned invalid {resource} pagination.",
        ) from exc


class TatumSuiAdapter(BaseBlockchainAdapter):
    """Read public Sui data through Tatum's authenticated GraphQL gateway."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        rpc_url: Optional[str] = None,
        network: Optional[str] = None,
    ):
        self.api_key = settings.TATUM_API_KEY if api_key is None else api_key
        self.rpc_url = (
            settings.TATUM_SUI_RPC_URL if rpc_url is None else rpc_url
        ).rstrip("/")
        self.graphql_url = (
            self.rpc_url
            if self.rpc_url.endswith("/graphql")
            else f"{self.rpc_url}/graphql"
        )
        self.network = settings.SUI_NETWORK if network is None else network

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    async def _post_graphql(
        self, query: str, variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute a GraphQL query and reject HTTP, GraphQL, and shape errors."""
        expected_chain_identifier = _SUI_CHAIN_IDENTIFIERS.get(
            self.network.casefold()
        )
        if expected_chain_identifier is None:
            raise TatumSuiError(
                "unsupported_network",
                "Tatum Sui chain identity is not verified for this network.",
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self.graphql_url,
                    json={"query": query, "variables": variables or {}},
                    headers=self._headers(),
                )
        except httpx.TimeoutException as exc:
            raise TatumSuiError("timeout", "Tatum Sui request timed out.") from exc
        except httpx.RequestError as exc:
            raise TatumSuiError(
                "connection_error", "Could not connect to the Tatum Sui endpoint."
            ) from exc

        if not response.is_success:
            if response.status_code in {401, 403}:
                raise TatumSuiError(
                    "authentication_error",
                    "Tatum Sui authentication was rejected.",
                )
            raise TatumSuiError(
                f"http_{response.status_code}",
                f"Tatum Sui endpoint returned HTTP {response.status_code}.",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise TatumSuiError(
                "invalid_json", "Tatum Sui endpoint returned invalid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise TatumSuiError(
                "invalid_response", "Tatum Sui endpoint returned an invalid response."
            )
        if payload.get("errors"):
            raise TatumSuiError(
                "graphql_error", "Tatum Sui rejected the GraphQL query."
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise TatumSuiError(
                "invalid_response",
                "Tatum Sui response did not contain GraphQL data.",
            )
        chain_identifier = data.get("chainIdentifier")
        if not isinstance(chain_identifier, str) or not chain_identifier:
            raise TatumSuiError(
                "invalid_response",
                "Tatum Sui response did not identify a chain.",
            )
        if chain_identifier != expected_chain_identifier:
            raise TatumSuiError(
                "network_mismatch",
                "Tatum Sui endpoint is connected to a different network than configured.",
            )
        return data

    async def check_readiness(self) -> Dict[str, Any]:
        """Verify live Sui GraphQL access using the latest mainnet checkpoint."""
        result: Dict[str, Any] = {
            "configured": bool(self.api_key),
            "endpoint": self.rpc_url,
            "network": self.network,
        }
        if not self.api_key:
            result.update(
                status="unconfigured",
                connected=False,
                message="TATUM_API_KEY is not set in environment.",
            )
            return result

        try:
            checkpoint = await self.get_latest_checkpoint()
        except TatumSuiError as exc:
            result.update(
                status=(
                    "degraded"
                    if exc.code in {
                        "graphql_error",
                        "invalid_json",
                        "invalid_response",
                        "network_mismatch",
                        "unsupported_network",
                        "authentication_error",
                    }
                    else "unreachable"
                ),
                connected=False,
                error=exc.code,
                message=str(exc),
            )
            logger.warning("Tatum Sui readiness check failed: %s", exc.code)
            return result

        result.update(
            status="ready",
            connected=True,
            latest_checkpoint=checkpoint,
        )
        return result

    async def get_latest_checkpoint(self) -> int:
        """Return the latest checkpoint sequence number from Sui GraphQL."""
        data = await self._post_graphql(
            """
            query LatestCheckpoint {
              chainIdentifier
              checkpoints(last: 1) {
                nodes {
                  sequenceNumber
                }
              }
            }
            """
        )
        checkpoints = data.get("checkpoints")
        nodes = checkpoints.get("nodes") if isinstance(checkpoints, dict) else None
        if not isinstance(nodes, list) or not nodes or not isinstance(nodes[-1], dict):
            raise TatumSuiError(
                "invalid_response", "Tatum Sui response did not contain a checkpoint."
            )
        sequence = nodes[-1].get("sequenceNumber")
        try:
            checkpoint = int(sequence)
        except (TypeError, ValueError) as exc:
            raise TatumSuiError(
                "invalid_response", "Tatum Sui returned an invalid checkpoint."
            ) from exc
        if checkpoint < 0 or str(sequence) != str(checkpoint):
            raise TatumSuiError(
                "invalid_response", "Tatum Sui returned an invalid checkpoint."
            )
        return checkpoint

    async def get_balance(self, sui_address: str) -> Dict[str, Any]:
        """Fetch a public address's SUI balance in MIST and SUI."""
        data = await self._post_graphql(
            """
            query SuiBalance($address: SuiAddress!) {
              chainIdentifier
              address(address: $address) {
                balance(coinType: "0x2::sui::SUI") {
                  totalBalance
                }
              }
            }
            """,
            {"address": sui_address},
        )
        address = data.get("address")
        balance = address.get("balance") if isinstance(address, dict) else None
        if not isinstance(balance, dict):
            raise TatumSuiError(
                "invalid_response", "Tatum Sui response did not contain an address balance."
            )
        raw_balance = balance.get("totalBalance")
        if (
            isinstance(raw_balance, bool)
            or not isinstance(raw_balance, (str, int))
            or not str(raw_balance).isascii()
            or not str(raw_balance).isdigit()
        ):
            raise TatumSuiError(
                "invalid_response", "Tatum Sui returned an invalid address balance."
            )
        try:
            total_mist = int(raw_balance)
        except ValueError as exc:
            raise TatumSuiError(
                "invalid_response", "Tatum Sui returned an invalid address balance."
            ) from exc
        if total_mist < 0:
            raise TatumSuiError(
                "invalid_response", "Tatum Sui returned an invalid address balance."
            )
        with localcontext() as context:
            context.prec = max(28, len(str(total_mist)) + 12)
            display_balance = format(
                Decimal(total_mist).scaleb(-9), ".4f"
            )
        return {
            "sui_address": sui_address,
            "total_balance_sui": display_balance,
            "raw_balance_mist": str(total_mist),
            "network": self.network,
        }

    async def get_wallet_balances(
        self,
        sui_address: str,
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch one bounded page of all coin-type balances owned by an address."""
        limit, cursor = _pagination_values(limit, cursor)
        data = await self._post_graphql(
            """
            query SuiAddressBalances(
              $address: SuiAddress!
              $first: Int!
              $after: String
            ) {
              chainIdentifier
              address(address: $address) {
                balances(first: $first, after: $after) {
                  nodes {
                    coinType {
                      repr
                    }
                    totalBalance
                    coinMetadata {
                      decimals
                      symbol
                    }
                  }
                  pageInfo {
                    hasNextPage
                    endCursor
                  }
                }
              }
            }
            """,
            {"address": sui_address, "first": limit, "after": cursor},
        )
        address = data.get("address")
        connection = address.get("balances") if isinstance(address, dict) else None
        if not isinstance(connection, dict):
            raise TatumSuiError(
                "invalid_response",
                "Tatum Sui response did not contain address balances.",
            )
        nodes = connection.get("nodes")
        page_info = connection.get("pageInfo")
        if not isinstance(nodes, list) or not isinstance(page_info, dict):
            raise TatumSuiError(
                "invalid_response",
                "Tatum Sui returned malformed address balance pagination.",
            )
        pagination = _pagination_metadata(page_info, "address balance")

        balances: List[Dict[str, Any]] = []
        for item in nodes:
            coin_type_data = item.get("coinType") if isinstance(item, dict) else None
            coin_type = (
                coin_type_data.get("repr")
                if isinstance(coin_type_data, dict)
                else None
            )
            total_balance = item.get("totalBalance") if isinstance(item, dict) else None
            if (
                not isinstance(coin_type, str)
                or not coin_type
                or not isinstance(total_balance, str)
                or not total_balance.isascii()
                or not total_balance.isdigit()
            ):
                raise TatumSuiError(
                    "invalid_response",
                    "Tatum Sui returned an invalid coin balance.",
                )

            metadata = item.get("coinMetadata")
            decimals = metadata.get("decimals") if isinstance(metadata, dict) else None
            symbol = metadata.get("symbol") if isinstance(metadata, dict) else None
            balance: Dict[str, Any] = {
                "coin_type": coin_type,
                "raw_balance": total_balance,
                "amount": None,
                "decimals": None,
                "symbol": None,
            }
            if metadata is not None and not isinstance(metadata, dict):
                raise TatumSuiError(
                    "invalid_response",
                    "Tatum Sui returned malformed coin metadata.",
                )
            if decimals is not None:
                if (
                    isinstance(decimals, bool)
                    or not isinstance(decimals, int)
                    or not 0 <= decimals <= 255
                ):
                    raise TatumSuiError(
                        "invalid_response",
                        "Tatum Sui returned invalid coin decimals.",
                    )
                balance["decimals"] = decimals
                balance["amount"] = _decimal_amount(total_balance, decimals)
            if symbol is not None:
                if not isinstance(symbol, str):
                    raise TatumSuiError(
                        "invalid_response",
                        "Tatum Sui returned an invalid coin symbol.",
                    )
                balance["symbol"] = symbol
            balances.append(balance)

        return {
            "sui_address": sui_address,
            "network": self.network,
            "balances": balances,
            "pagination": pagination.model_dump(),
        }

    async def get_owned_objects(
        self,
        sui_address: str,
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch one bounded page of objects owned by an address."""
        limit, cursor = _pagination_values(limit, cursor)
        data = await self._post_graphql(
            """
            query SuiOwnedObjects(
              $address: SuiAddress!
              $first: Int!
              $after: String
            ) {
              chainIdentifier
              address(address: $address) {
                objects(
                  first: $first
                  after: $after
                  filter: { owner: $address }
                ) {
                  nodes {
                    address
                    version
                    digest
                    contents {
                      type {
                        repr
                      }
                    }
                  }
                  pageInfo {
                    hasNextPage
                    endCursor
                  }
                }
              }
            }
            """,
            {"address": sui_address, "first": limit, "after": cursor},
        )
        address = data.get("address")
        connection = address.get("objects") if isinstance(address, dict) else None
        if not isinstance(connection, dict):
            raise TatumSuiError(
                "invalid_response",
                "Tatum Sui response did not contain owned objects.",
            )
        nodes = connection.get("nodes")
        page_info = connection.get("pageInfo")
        if not isinstance(nodes, list) or not isinstance(page_info, dict):
            raise TatumSuiError(
                "invalid_response",
                "Tatum Sui returned malformed owned-object pagination.",
            )
        pagination = _pagination_metadata(page_info, "owned-object")

        objects: List[Dict[str, Any]] = []
        for item in nodes:
            contents = item.get("contents") if isinstance(item, dict) else None
            move_type = contents.get("type") if isinstance(contents, dict) else None
            object_type = move_type.get("repr") if isinstance(move_type, dict) else None
            object_id = item.get("address") if isinstance(item, dict) else None
            version = item.get("version") if isinstance(item, dict) else None
            digest = item.get("digest") if isinstance(item, dict) else None
            if (
                not isinstance(object_id, str)
                or not object_id
                or (version is not None and not isinstance(version, (str, int)))
                or isinstance(version, bool)
                or (digest is not None and not isinstance(digest, str))
                or (object_type is not None and not isinstance(object_type, str))
            ):
                raise TatumSuiError(
                    "invalid_response",
                    "Tatum Sui returned an invalid owned object.",
                )
            if version is not None and (
                not str(version).isascii() or not str(version).isdigit()
            ):
                raise TatumSuiError(
                    "invalid_response",
                    "Tatum Sui returned an invalid owned object version.",
                )
            objects.append(
                {
                    "object_id": object_id,
                    "version": str(version) if version is not None else None,
                    "digest": digest,
                    "type": object_type,
                }
            )

        return {
            "sui_address": sui_address,
            "network": self.network,
            "objects": objects,
            "pagination": pagination.model_dump(),
        }

    async def get_recent_activity(
        self, sui_address: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Fetch recent sent transactions for a public Sui address."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("Sui activity limit must be between 1 and 50.")

        data = await self._post_graphql(
            """
            query RecentSuiActivity($address: SuiAddress!, $limit: Int!) {
              chainIdentifier
              address(address: $address) {
                transactions(
                  last: $limit
                  filter: { sentAddress: $address }
                ) {
                  nodes {
                    digest
                    effects {
                      timestamp
                      status
                    }
                  }
                }
              }
            }
            """,
            {"address": sui_address, "limit": limit},
        )
        address = data.get("address")
        connection = address.get("transactions") if isinstance(address, dict) else None
        nodes = connection.get("nodes") if isinstance(connection, dict) else None
        if not isinstance(nodes, list):
            raise TatumSuiError(
                "invalid_response", "Tatum Sui response did not contain transactions."
            )
        if len(nodes) > limit:
            raise TatumSuiError(
                "invalid_response", "Tatum Sui returned too many transactions."
            )

        activity: List[Dict[str, Any]] = []
        for item in nodes:
            effects = item.get("effects") if isinstance(item, dict) else None
            if not isinstance(item, dict) or not isinstance(item.get("digest"), str):
                raise TatumSuiError(
                    "invalid_response", "Tatum Sui returned an invalid transaction."
                )
            if effects is not None and not isinstance(effects, dict):
                raise TatumSuiError(
                    "invalid_response", "Tatum Sui returned invalid transaction effects."
                )
            timestamp_ms = None
            status = "unknown"
            if isinstance(effects, dict):
                timestamp = effects.get("timestamp")
                if timestamp is not None:
                    if not isinstance(timestamp, str):
                        raise TatumSuiError(
                            "invalid_response",
                            "Tatum Sui returned an invalid transaction timestamp.",
                        )
                    try:
                        transaction_time = datetime.fromisoformat(
                            timestamp.replace("Z", "+00:00")
                        )
                        if transaction_time.utcoffset() is None:
                            raise ValueError("Timestamp must include a timezone.")
                        timestamp_ms = int(
                            transaction_time.timestamp() * 1000
                        )
                    except (AttributeError, TypeError, ValueError) as exc:
                        raise TatumSuiError(
                            "invalid_response",
                            "Tatum Sui returned an invalid transaction timestamp.",
                        ) from exc
                effect_status = effects.get("status")
                if effect_status is not None:
                    if not isinstance(effect_status, str):
                        raise TatumSuiError(
                            "invalid_response",
                            "Tatum Sui returned an invalid transaction status.",
                        )
                    status = effect_status.lower()
            try:
                transaction = SuiTransactionResponse.model_validate({
                    "digest": item["digest"],
                    "timestamp_ms": timestamp_ms,
                    "status": status,
                })
            except ValidationError as exc:
                raise TatumSuiError(
                    "invalid_response", "Tatum Sui returned an invalid transaction."
                ) from exc
            activity.append(transaction.model_dump())
        return activity
