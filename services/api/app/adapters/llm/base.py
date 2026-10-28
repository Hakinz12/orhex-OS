"""Abstract base interface for LLM provider adapters."""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BaseLLMAdapter(ABC):
    """
    Modular abstraction layer for LLM integration.
    Guarantees the system logic remains decoupled from specific AI vendors.
    """

    @abstractmethod
    async def check_readiness(self) -> Dict[str, Any]:
        """Verify API key configuration and provider connectivity."""
        pass

    @abstractmethod
    async def generate_response(
        self,
        system_prompt: str,
        user_message: str,
        recalled_memories: List[Dict[str, Any]],
        chain_context: Optional[Dict[str, Any]] = None,
        goals_context: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Generate grounded conversational response with citations and recommendations."""
        pass

    @abstractmethod
    async def extract_candidate_memories(
        self,
        user_message: str
    ) -> List[Dict[str, Any]]:
        """Extract atomic goals, preferences, or commitments from user message."""
        pass

