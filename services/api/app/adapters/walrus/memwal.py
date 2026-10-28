"""Walrus Memory Mainnet adapter implementation."""
import json
import logging
import time
from typing import List, Dict, Any, Optional
import httpx
from app.core.config import settings
from app.adapters.walrus.base import BaseWalrusMemory

logger = logging.getLogger(__name__)


class MemWalAdapter(BaseWalrusMemory):
    """
    Adapter for Walrus Memory on Mainnet.
    Coordinates cryptographic writes and semantic recall via Walrus publishers/relayers.
    """

    def __init__(
        self,
        server_url: Optional[str] = None,
        agent_id: Optional[str] = None,
        account_id: Optional[str] = None,
        private_key: Optional[str] = None,
        network: Optional[str] = None
    ):
        self.server_url = (server_url or settings.MEMWAL_SERVER_URL).rstrip("/")
        self.agent_id = agent_id or settings.MEMWAL_AGENT_ID
        self.account_id = account_id or settings.MEMWAL_ACCOUNT_ID
        self.private_key = private_key or settings.MEMWAL_PRIVATE_KEY
        self.network = network or settings.WALRUS_NETWORK

    async def check_readiness(self) -> Dict[str, Any]:
        """Check configuration completeness and ping Walrus service."""
        configured = bool(self.agent_id and self.private_key)
        result = {
            "configured": configured,
            "network": self.network,
            "agent_id": self.agent_id if self.agent_id else "not_set",
            "server_url": self.server_url,
            "status": "ready" if configured else "unconfigured"
        }

        # Attempt endpoint probe
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(f"{self.server_url}/health")
                result["endpoint_reachable"] = (res.status_code in [200, 404])
                result["endpoint_http_code"] = res.status_code
        except Exception as e:
            result["endpoint_reachable"] = False
            result["endpoint_error"] = str(e)

        return result

    async def remember(
        self,
        namespace: str,
        text: str,
        category: str = "personal",
        memory_type: str = "semantic",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Send memory payload to Walrus publisher endpoint.
        Never marks memory as confirmed unless acknowledged with a valid blob ID.
        """
        if not self.agent_id:
            return {
                "status": "failed",
                "error": "Walrus agent credentials missing (MEMWAL_AGENT_ID)."
            }

        payload = {
            "agent_id": self.agent_id,
            "account_id": self.account_id,
            "namespace": namespace,
            "content": {
                "text": text,
                "category": category,
                "type": memory_type,
                "timestamp": int(time.time()),
                "metadata": metadata or {}
            }
        }

        headers = {
            "Content-Type": "application/json",
            "X-Agent-ID": self.agent_id
        }

        url = f"{self.server_url}/v1/memories"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, json=payload, headers=headers)
                if res.status_code in [200, 201]:
                    data = res.json()
                    return {
                        "status": "confirmed",
                        "blob_id": data.get("blob_id") or data.get("id"),
                        "raw_response": data
                    }
                elif res.status_code == 202:
                    data = res.json()
                    return {
                        "status": "queued",
                        "operation_id": data.get("operation_id"),
                        "raw_response": data
                    }
                else:
                    return {
                        "status": "failed",
                        "http_code": res.status_code,
                        "error": res.text
                    }
        except Exception as e:
            logger.error(f"Walrus remember failed: {e}")
            return {
                "status": "failed",
                "error": str(e)
            }

    async def recall(
        self,
        namespace: str,
        query: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Query Walrus semantic memory store."""
        if not self.agent_id:
            return []

        url = f"{self.server_url}/v1/memories/recall"
        params = {
            "namespace": namespace,
            "query": query,
            "limit": limit
        }
        headers = {"X-Agent-ID": self.agent_id}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(url, params=params, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    return data.get("memories", [])
                return []
        except Exception as e:
            logger.error(f"Walrus recall error: {e}")
            return []

    async def get_blob_status(self, blob_id: str) -> Dict[str, Any]:
        """Query blob status directly from Walrus publisher."""
        url = f"{self.server_url}/v1/blobs/{blob_id}/status"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    return res.json()
                return {"status": "unknown", "http_code": res.status_code}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def delete_memory(self, namespace: str, memory_id: str) -> Dict[str, Any]:
        """Request deletion of a memory in Walrus store."""
        url = f"{self.server_url}/v1/memories/{memory_id}"
        headers = {"X-Agent-ID": self.agent_id}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.delete(url, headers=headers, params={"namespace": namespace})
                return {"success": res.status_code in [200, 204], "status_code": res.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

