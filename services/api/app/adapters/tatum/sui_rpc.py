"""Tatum Sui GraphQL adapter for read-only blockchain intelligence."""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.adapters.tatum.base import BaseBlockchainAdapter
from app.core.config import settings

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
        if not isinstance(data.get("chainIdentifier"), str) or not data["chainIdentifier"]:
            raise TatumSuiError(
                "invalid_response", "Tatum Sui response did not identify a chain."
            )
        expected_chain_identifier = _SUI_CHAIN_IDENTIFIERS.get(self.network.casefold())
        if (
            expected_chain_identifier is not None
            and data["chainIdentifier"] != expected_chain_identifier
        ):
            raise TatumSuiError(
                "network_mismatch",
                "Tatum Sui endpoint is connected to a different network than configured.",
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
        try:
            total_mist = int(balance["totalBalance"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TatumSuiError(
                "invalid_response", "Tatum Sui returned an invalid address balance."
            ) from exc
        if total_mist < 0:
            raise TatumSuiError(
                "invalid_response", "Tatum Sui returned an invalid address balance."
            )
        return {
            "sui_address": sui_address,
            "total_balance_sui": f"{total_mist / 1_000_000_000:.4f}",
            "raw_balance_mist": str(total_mist),
            "network": self.network,
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

        activity: List[Dict[str, Any]] = []
        for item in nodes:
            effects = item.get("effects") if isinstance(item, dict) else None
            if not isinstance(item, dict) or not isinstance(item.get("digest"), str):
                raise TatumSuiError(
                    "invalid_response", "Tatum Sui returned an invalid transaction."
                )
            timestamp_ms = None
            status = "unknown"
            if isinstance(effects, dict):
                timestamp = effects.get("timestamp")
                if timestamp is not None:
                    try:
                        timestamp_ms = int(
                            datetime.fromisoformat(
                                timestamp.replace("Z", "+00:00")
                            ).timestamp()
                            * 1000
                        )
                    except (AttributeError, TypeError, ValueError) as exc:
                        raise TatumSuiError(
                            "invalid_response",
                            "Tatum Sui returned an invalid transaction timestamp.",
                        ) from exc
                if isinstance(effects.get("status"), str):
                    status = effects["status"].lower()
            activity.append(
                {
                    "digest": item["digest"],
                    "timestamp_ms": timestamp_ms,
                    "status": status,
                }
            )
        return activity
