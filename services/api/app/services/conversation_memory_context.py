"""Bounded, owner-scoped memory context for conversational grounding."""
import logging
import math
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.walrus.base import BaseWalrusMemory
from app.services.memory_persistence import MemoryPersistenceError, recall_for_user

logger = logging.getLogger("orhex.conversation.memory_context")

MAX_MEMORY_CONTEXT_ITEMS = 5
MAX_MEMORY_ITEM_CHARACTERS = 3000
MAX_MEMORY_CONTEXT_CHARACTERS = 10_000


class RetrievedMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str | None = None
    content: str
    source: Literal["walrus"] = "walrus"
    score: float | None = None
    distance: float | None = None
    created_at: str | None = None


class ConversationMemoryContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["retrieved", "empty", "unavailable"]
    source: Literal["walrus"] = "walrus"
    memories: list[RetrievedMemory] = Field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.memories)


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _created_at(value: object) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str) and value.strip() and len(value) <= 128:
        return value
    return None


async def retrieve_conversation_memory_context(
    session: AsyncSession,
    walrus: BaseWalrusMemory,
    *,
    user_id: str,
    query: str,
) -> ConversationMemoryContext:
    """Recall from the authenticated user's derived namespace and filter local Trash."""
    try:
        hits = await recall_for_user(
            session,
            walrus,
            user_id=user_id,
            query=query,
            limit=MAX_MEMORY_CONTEXT_ITEMS,
        )
    except MemoryPersistenceError as exc:
        logger.warning("Owner-scoped conversation memory recall failed: %s", type(exc).__name__)
        return ConversationMemoryContext(status="unavailable")
    except Exception as exc:
        logger.warning("Conversation memory recall failed: %s", type(exc).__name__)
        return ConversationMemoryContext(status="unavailable")

    memories: list[RetrievedMemory] = []
    total_characters = 0
    for hit in hits[:MAX_MEMORY_CONTEXT_ITEMS]:
        if not isinstance(hit, dict):
            continue
        content = hit.get("text")
        if not isinstance(content, str) or not content.strip():
            continue
        content = content.strip()[:MAX_MEMORY_ITEM_CHARACTERS]
        remaining = MAX_MEMORY_CONTEXT_CHARACTERS - total_characters
        if remaining <= 0:
            break
        content = content[:remaining]
        if not content:
            continue
        memory_id = hit.get("blob_id")
        memory_id = memory_id if isinstance(memory_id, str) and memory_id else None
        memories.append(
            RetrievedMemory(
                memory_id=memory_id,
                content=content,
                score=_finite_number(hit.get("score")),
                distance=_finite_number(hit.get("distance")),
                created_at=_created_at(hit.get("created_at")),
            )
        )
        total_characters += len(content)

    return ConversationMemoryContext(
        status="retrieved" if memories else "empty",
        memories=memories,
    )
