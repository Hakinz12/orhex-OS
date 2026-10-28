"""Tatum Sui JSON-RPC adapter for real-time read-only blockchain intelligence."""
import logging
from typing import List, Dict, Any, Optional
import httpx
from app.core.config import settings
from app.adapters.tatum.base import BaseBlockchainAdapter

logger = logging.getLogger(__name__)


class TatumSuiAdapter(BaseBlockchainAdapter):
    """
    Adapter interfacing with Sui Mainnet via Tatum's RPC Gateway.
    Strictly read-only; queries public balances and transaction activity.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        rpc_url: Optional[str] = None,
        network: Optional[str] = None
    ):
        self.api_key = api_key or settings.TATUM_API_KEY
        self.rpc_url = (rpc_url or settings.TATUM_SUI_RPC_URL).rstrip("/")
        self.network = network or settings.SUI_NETWORK

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    async def _post_rpc(self, method: str, params: List[Any]) -> Dict[str, Any]:
        """Execute a standard JSON-RPC 2.0 call against Tatum Sui endpoint."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(self.rpc_url, json=payload, headers=self._headers())
            res.raise_for_status()
            return res.json()

    async def check_readiness(self) -> Dict[str, Any]:
        """Verify Tatum RPC responsiveness by querying the latest checkpoint."""
        if not self.api_key:
            return {
                "configured": False,
                "endpoint": self.rpc_url,
                "network": self.network,
                "status": "unconfigured",
                "message": "TATUM_API_KEY is not set in environment."
            }

        result = {
            "configured": True,
            "endpoint": self.rpc_url,
            "network": self.network,
            "status": "ready"
        }

        try:
            checkpoint = await self.get_latest_checkpoint()
            if checkpoint is not None:
                result["status"] = "ready"
                result["latest_checkpoint"] = checkpoint
                result["connected"] = True
            else:
                result["status"] = "degraded"
                result["connected"] = False
        except Exception as e:
            logger.warning(f"Tatum readiness check warning: {e}")
            result["status"] = "unreachable"
            result["connected"] = False
            result["error"] = str(e)

        return result

    async def get_latest_checkpoint(self) -> Optional[int]:
        """Fetch latest sequence number from Sui node."""
        try:
            data = await self._post_rpc("sui_getLatestCheckpointSequenceNumber", [])
            if "result" in data:
                return int(data["result"])
        except Exception as e:
            logger.error(f"Failed to fetch Sui checkpoint: {e}")
        return None

    async def get_balance(self, sui_address: str) -> Dict[str, Any]:
        """
        Fetch SUI balance for given address.
        SUI has 9 decimal places (1 SUI = 1,000,000,000 MIST).
        """
        try:
            data = await self._post_rpc(
                "suix_getBalance",
                [sui_address, "0x2::sui::SUI"]
            )
            if "result" in data:
                res = data["result"]
                total_mist = int(res.get("totalBalance", 0))
                sui_val = total_mist / 1_000_000_000.0
                return {
                    "sui_address": sui_address,
                    "total_balance_sui": f"{sui_val:.4f}",
                    "raw_balance_mist": str(total_mist),
                    "coin_object_count": res.get("coinObjectCount", 0),
                    "network": self.network
                }
        except Exception as e:
            logger.error(f"Failed to fetch balance for {sui_address}: {e}")

        return {
            "sui_address": sui_address,
            "total_balance_sui": "0.0000",
            "raw_balance_mist": "0",
            "coin_object_count": 0,
            "network": self.network,
            "error": "Unable to query balance via Tatum RPC"
        }

    async def get_recent_activity(
        self,
        sui_address: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Fetch recent transaction digest details for an address."""
        try:
            query_filter = {"FromAddress": sui_address}
            data = await self._post_rpc(
                "suix_queryTransactionBlocks",
                [
                    {"filter": query_filter, "options": {"showEffects": True, "showInput": True}},
                    None,
                    limit,
                    True
                ]
            )
            if "result" in data and "data" in data["result"]:
                tx_list = []
                for item in data["result"]["data"]:
                    tx_list.append({
                        "digest": item.get("digest"),
                        "timestamp_ms": item.get("timestampMs"),
                        "status": item.get("effects", {}).get("status", {}).get("status", "unknown")
                    })
                return tx_list
        except Exception as e:
            logger.error(f"Failed to query Sui transactions: {e}")

        return []
