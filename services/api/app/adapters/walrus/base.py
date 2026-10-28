"""Abstract base interface for Walrus Memory adapters."""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BaseWalrusMemory(ABC):
    """
    Modular interface for Walrus decentralized memory persistence.
    Operates on Walrus Mainnet with Ed25519 authenticated delegate keys.
    """

    @abstractmethod
    async def check_readiness(self) -> Dict[str, Any]:
        """Verify Walrus endpoint liveness and delegated credentials."""
        pass

    @abstractmethod
    async def remember(
        self,
        namespace: str,
        text: str,
        category: str = "personal",
        memory_type: str = "semantic",
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Store a verified atomic memory blob in Walrus storage.
        Returns a status dictionary, including a blob ID when confirmed.
        """
        pass

    @abstractmethod
    async def recall(
        self,
        namespace: str,
        query: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Query Walrus Memory for memories relevant to the query in the given namespace.
        """
        pass

    @abstractmethod
    async def get_blob_status(self, blob_id: str) -> Dict[str, Any]:
        """Check blob status, or explicitly report when this operation is unsupported."""
        pass

    @abstractmethod
    async def get_remember_status(self, job_id: str) -> Dict[str, Any]:
        """Check an accepted write job to reconcile an uncertain operation."""
        pass

    @abstractmethod
    async def delete_memory(self, namespace: str, memory_id: str) -> Dict[str, Any]:
        """Delete a memory, or explicitly report when this operation is unsupported."""
        pass
