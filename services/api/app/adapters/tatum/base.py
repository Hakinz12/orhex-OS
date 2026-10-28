"""Abstract base interface for blockchain intelligence adapters."""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BaseBlockchainAdapter(ABC):
    """
    Modular interface for blockchain intelligence.
    Provides read-only queries into Sui Mainnet via Tatum RPC.
    """

    @abstractmethod
    async def check_readiness(self) -> Dict[str, Any]:
        """Verify Tatum RPC connection and API key validity."""
        pass

    @abstractmethod
    async def get_latest_checkpoint(self) -> Optional[int]:
        """Retrieve current synchronized checkpoint sequence number on Sui."""
        pass

    @abstractmethod
    async def get_balance(self, sui_address: str) -> Dict[str, Any]:
        """Fetch balance for SUI native coin."""
        pass

    @abstractmethod
    async def get_recent_activity(
        self,
        sui_address: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Fetch recent read-only transaction activity associated with the address."""
        pass

