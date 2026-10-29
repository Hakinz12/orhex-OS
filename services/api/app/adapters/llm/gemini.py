"""Google Gemini LLM Adapter implementation using asynchronous HTTP client."""
import json
import logging
from typing import List, Dict, Any, Optional
import httpx
from app.core.config import settings
from app.adapters.llm.base import BaseLLMAdapter

logger = logging.getLogger(__name__)


class GeminiAdapter(BaseLLMAdapter):
    """
    Adapter for Google Gemini API (Gemini 2.5 Flash).
    Communicates via Google's v1beta REST API with strong schema validation and timeouts.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = settings.GEMINI_API_KEY if api_key is None else api_key
        self.model = settings.GEMINI_MODEL if model is None else model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    async def check_readiness(self) -> Dict[str, Any]:
        """Perform a non-destructive handshake to verify API key and model availability."""
        if not self.api_key:
            return {
                "status": "unconfigured",
                "message": "GEMINI_API_KEY is not set in environment.",
                "model": self.model
            }

        url = f"{self.base_url}/{self.model}?key={self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    return {
                        "status": "ready",
                        "model": self.model,
                        "display_name": data.get("displayName", self.model),
                        "supported_generation_methods": data.get("supportedGenerationMethods", [])
                    }
                else:
                    return {
                        "status": "error",
                        "status_code": res.status_code,
                        "message": res.text,
                        "model": self.model
                    }
        except Exception as e:
            logger.error(f"Gemini readiness check failed: {e}")
            return {
                "status": "unreachable",
                "error": str(e),
                "model": self.model
            }

    async def generate_response(
        self,
        system_prompt: str,
        user_message: str,
        recalled_memories: List[Dict[str, Any]],
        chain_context: Optional[Dict[str, Any]] = None,
        goals_context: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Assemble structured prompt and query Gemini."""
        if not self.api_key:
            return {
                "answer": "ORHEX OS is awaiting a valid GEMINI_API_KEY. Please configure your key in .env.",
                "evidence": [],
                "recommendations": [],
                "candidate_memories": []
            }

        # Context assembly
        memory_str = json.dumps(recalled_memories, indent=2) if recalled_memories else "None"
        chain_str = json.dumps(chain_context, indent=2) if chain_context else "None"
        goals_str = json.dumps(goals_context, indent=2) if goals_context else "None"

        full_prompt = (
            f"{system_prompt}\n\n"
            f"--- RECALLED MEMORIES ---\n{memory_str}\n\n"
            f"--- SUI BLOCKCHAIN STATE ---\n{chain_str}\n\n"
            f"--- ACTIVE GOALS ---\n{goals_str}\n\n"
            f"USER QUERY: {user_message}\n\n"
            f"Return a structured JSON with: 'answer' (markdown string), "
            f"'evidence' (list of {{kind, reference, summary}}), and "
            f"'recommendations' (list of {{title, reason, source_memory_id}})."
        )

        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [
                {
                    "parts": [{"text": full_prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json"
            }
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    candidates = data.get("candidates", [])
                    if candidates and "content" in candidates[0]:
                        raw_text = candidates[0]["content"]["parts"][0]["text"]
                        try:
                            parsed = json.loads(raw_text)
                            return parsed
                        except json.JSONDecodeError:
                            return {
                                "answer": raw_text,
                                "evidence": [],
                                "recommendations": []
                            }
                return {
                    "answer": f"Gemini API returned status {res.status_code}: {res.text}",
                    "evidence": [],
                    "recommendations": []
                }
        except Exception as e:
            logger.error(f"Gemini generate_response error: {e}")
            return {
                "answer": f"Error communicating with Gemini: {str(e)}",
                "evidence": [],
                "recommendations": []
            }

    async def extract_candidate_memories(self, user_message: str) -> List[Dict[str, Any]]:
        """Extract atomic facts, preferences, or commitments from conversational input."""
        if not self.api_key:
            return []

        extract_prompt = (
            "Analyze the following user input and identify any explicit personal facts, "
            "commitments, deadlines, or preferences that are worth remembering.\n"
            "Do NOT remember trivial greetings or fleeting queries.\n"
            "Return a JSON list of objects with: 'text', 'category' (personal|goals|commitments|work), "
            f"'type' (semantic|commitment|preference), 'confidence' (0.0 to 1.0).\n\n"
            f"User message: {user_message}"
        )

        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": extract_prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json"
            }
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    raw = data["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(raw)
                    return parsed if isinstance(parsed, list) else []
        except Exception as e:
            logger.error(f"Gemini extract_candidate_memories error: {e}")
        return []
