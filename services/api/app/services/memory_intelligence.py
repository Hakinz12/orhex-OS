"""Contextual memory decision layer for ORHEX.

This module interprets a new memory against relevant owner-scoped context and
chooses a safe action without overwriting historical records. The action is a
validated structured decision: CREATE, ENRICH, UPDATE, CORRECT, or
PRESERVE_HISTORY.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from enum import Enum
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.adapters.llm.gemini import GeminiAdapter
from app.models.domain import MemorySyncRecord

_VALID_DECISIONS = {"CREATE", "ENRICH", "UPDATE", "CORRECT", "PRESERVE_HISTORY"}
_USER_DECISION_LOCKS: dict[str, asyncio.Lock] = {}


def _get_user_lock(user_id: str) -> asyncio.Lock:
    lock = _USER_DECISION_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _USER_DECISION_LOCKS[user_id] = lock
    return lock


class MemoryDecisionType(str, Enum):
    CREATE = "CREATE"
    ENRICH = "ENRICH"
    UPDATE = "UPDATE"
    CORRECT = "CORRECT"
    PRESERVE_HISTORY = "PRESERVE_HISTORY"


class ContextualMemoryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: MemoryDecisionType
    affected_memory_ids: list[str] = Field(default_factory=list)
    proposed_content: str
    reasoning_summary: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    original_text: str
    verbatim_request: str | None = None
    preserve_history: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    memory_id: str | None = None

    @field_validator("proposed_content", "original_text")
    @classmethod
    def _require_content(cls, value: str) -> str:
        stripped = str(value).strip()
        if not stripped:
            raise ValueError("Memory content cannot be empty.")
        return stripped


def _normalize_memory_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _tokenize(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _normalize_memory_text(value))
        if len(token) > 2
    }


def _memory_overlap_score(first: str, second: str) -> float:
    tokens_a = _tokenize(first)
    tokens_b = _tokenize(second)
    if not tokens_a or not tokens_b:
        return 0.0
    overlap = len(tokens_a & tokens_b)
    return overlap / max(len(tokens_a), len(tokens_b))


def _looks_temporary_change(text: str) -> bool:
    lower = text.lower()
    markers = (
        "now",
        "currently",
        "today",
        "changed",
        "moved",
        "updated",
        "shifted",
        "previously",
        "used to",
        "starting",
        "later",
        "beginning",
        "was",
        "went from",
        "no longer",
    )
    return any(marker in lower for marker in markers)


def _shares_domain_context(new_text: str, existing_text: str) -> bool:
    new_tokens = _tokenize(new_text)
    existing_tokens = _tokenize(existing_text)
    if new_tokens & existing_tokens:
        return True
    new_lower = new_text.lower()
    existing_lower = existing_text.lower()
    for new_marker in ("moved", "live", "lives", "home", "city", "product", "manager", "role", "coffee", "tea"):
        if new_marker in new_lower and any(term in existing_lower for term in ("live", "lives", "home", "city", "product", "manager", "role", "coffee", "tea")):
            return True
    return False


def _is_history_update(new_text: str, existing_text: str) -> bool:
    if not _looks_temporary_change(new_text):
        return False
    return _shares_domain_context(new_text, existing_text)


def _is_unrelated(new_text: str, existing_text: str) -> bool:
    return _memory_overlap_score(new_text, existing_text) < 0.08


def _is_contradictory(new_text: str, existing_text: str) -> bool:
    overlap = _memory_overlap_score(new_text, existing_text)
    if overlap < 0.08:
        return False
    normalized_new = _normalize_memory_text(new_text)
    normalized_existing = _normalize_memory_text(existing_text)
    contradictory_markers = (
        "not",
        "never",
        "actually",
        "instead",
        "but",
        "wrong",
        "correction",
        "no longer",
        "used to",
        "previously",
        "moved",
    )
    has_contradiction_language = any(marker in normalized_new for marker in contradictory_markers)
    if has_contradiction_language:
        return True
    for marker in ("not ", "no longer", "used to", "instead of", "moved from"):
        if marker in normalized_new and normalized_existing:
            return True
    return False


def _serialize_memory_record(memory: Mapping[str, Any] | MemorySyncRecord) -> dict[str, Any]:
    if isinstance(memory, MemorySyncRecord):
        data = {
            "id": memory.id,
            "text": memory.text,
            "category": memory.category,
            "memory_type": memory.memory_type,
            "source": memory.source,
            "confidence": memory.confidence,
            "status": memory.status,
            "created_at": memory.created_at,
            "metadata": memory.metadata_json,
        }
    else:
        data = dict(memory)

    metadata = data.get("metadata")
    if isinstance(metadata, str):
        try:
            data["metadata"] = json.loads(metadata)
        except json.JSONDecodeError:
            data["metadata"] = {}
    return data


def _candidate_decision_from_payload(
    payload: Mapping[str, Any],
    *,
    original_text: str,
    relevant_context: Sequence[Mapping[str, Any]],
) -> ContextualMemoryDecision | None:
    if not isinstance(payload, Mapping):
        return None

    decision_value = payload.get("decision") or payload.get("action") or payload.get("classification")
    if decision_value is None:
        return None

    decision_text = str(decision_value).strip().upper()
    if decision_text not in _VALID_DECISIONS:
        return None

    affected = payload.get("affected_memory_ids") or payload.get("memory_ids") or []
    if isinstance(affected, str):
        affected = [affected]
    if not isinstance(affected, list) or any(not isinstance(item, str) for item in affected):
        return None
    affected_memory_ids = affected
    context_memory_ids = {
        str(entry.get("id"))
        for entry in relevant_context
        if entry.get("id") is not None
    }
    if any(memory_id not in context_memory_ids for memory_id in affected_memory_ids):
        return None
    if decision_text == "CREATE" and affected_memory_ids:
        return None
    if decision_text in {"ENRICH", "UPDATE", "CORRECT"} and not affected_memory_ids:
        return None

    proposed_content = (
        payload.get("proposed_content")
        or payload.get("content")
        or payload.get("memory_text")
        or original_text
    )
    if not isinstance(proposed_content, str):
        return None
    reasoning_summary = (
        payload.get("reasoning_summary")
        or payload.get("reasoning")
        or payload.get("summary")
        or "Gemini classified the memory input against related context."
    )
    if not isinstance(reasoning_summary, str):
        return None

    try:
        confidence = float(payload.get("confidence", 0.75))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        return None

    return ContextualMemoryDecision(
        decision=MemoryDecisionType(decision_text),
        affected_memory_ids=affected_memory_ids,
        proposed_content=str(proposed_content).strip(),
        reasoning_summary=str(reasoning_summary).strip(),
        confidence=confidence,
        original_text=original_text,
        verbatim_request=original_text,
        preserve_history=decision_text in {"UPDATE", "CORRECT", "PRESERVE_HISTORY"},
        metadata={"relevant_context_count": len(relevant_context), "source": "gemini"},
    )


async def _decide_with_gemini(
    *,
    new_memory_text: str,
    relevant_context: Sequence[Mapping[str, Any]],
    adapter: GeminiAdapter | None = None,
) -> ContextualMemoryDecision | None:
    if adapter is None:
        adapter = GeminiAdapter()

    try:
        readiness = await adapter.check_readiness()
    except Exception:
        return None
    if readiness is None or readiness.get("status") not in {"ready", "degraded"}:
        return None

    payload = {
        "new_memory": new_memory_text,
        "relevant_context": [
            {
                "id": entry.get("id"),
                "text": entry.get("text"),
                "category": entry.get("category"),
                "memory_type": entry.get("memory_type"),
                "created_at": str(entry.get("created_at")),
                "status": entry.get("status"),
                "metadata": entry.get("metadata"),
            }
            for entry in relevant_context[:8]
        ],
    }

    try:
        result = await adapter.generate_response(
            system_prompt=(
                "Classify the new memory against the user's memory context. Return JSON only with "
                "the fields decision, affected_memory_ids, proposed_content, reasoning_summary, "
                "confidence. Valid decisions are CREATE, ENRICH, UPDATE, CORRECT, "
                "PRESERVE_HISTORY. Preserve historical records and do not overwrite them."
            ),
            user_message=json.dumps(payload, sort_keys=True),
            recalled_memories=payload["relevant_context"],
            chain_context={"memory_decision_mode": "contextual"},
            goals_context=[],
        )
    except Exception:
        return None

    if not isinstance(result, Mapping):
        return None

    decoded = result
    if isinstance(result.get("answer"), str):
        answer_text = result["answer"]
        try:
            decoded = json.loads(answer_text)
        except json.JSONDecodeError:
            return None
    elif isinstance(result.get("answer"), Mapping):
        decoded = result["answer"]

    return _candidate_decision_from_payload(
        decoded,
        original_text=new_memory_text,
        relevant_context=relevant_context,
    )


def _heuristic_decision(
    *,
    new_memory_text: str,
    relevant_context: Sequence[Mapping[str, Any]],
) -> ContextualMemoryDecision:
    if not relevant_context:
        return ContextualMemoryDecision(
            decision=MemoryDecisionType.CREATE,
            affected_memory_ids=[],
            proposed_content=new_memory_text,
            reasoning_summary="No relevant memory context was found; preserve the new input as a standalone memory.",
            confidence=0.85,
            original_text=new_memory_text,
            verbatim_request=new_memory_text,
            preserve_history=False,
            metadata={"source": "heuristic"},
        )

    best_match = None
    best_score = 0.0
    for record in relevant_context:
        text = str(record.get("text") or "")
        score = _memory_overlap_score(new_memory_text, text)
        if best_match is None:
            best_match = record
            best_score = score
            continue
        if score > best_score:
            best_score = score
            best_match = record

    if best_match is not None and _is_history_update(new_memory_text, str(best_match.get("text") or "")):
        return ContextualMemoryDecision(
            decision=MemoryDecisionType.UPDATE,
            affected_memory_ids=[str(best_match.get("id"))] if best_match.get("id") else [],
            proposed_content=new_memory_text,
            reasoning_summary="The new memory captures a time-sensitive update to an existing fact while preserving the historical context.",
            confidence=0.92,
            original_text=new_memory_text,
            verbatim_request=new_memory_text,
            preserve_history=True,
            metadata={"source": "heuristic", "relatedness_score": best_score},
        )

    if best_match is None or _is_unrelated(new_memory_text, str(best_match.get("text") or "")):
        affected_ids = [str(best_match.get("id"))] if best_match and best_match.get("id") else []
        return ContextualMemoryDecision(
            decision=MemoryDecisionType.PRESERVE_HISTORY,
            affected_memory_ids=affected_ids,
            proposed_content=new_memory_text,
            reasoning_summary="The new memory is not meaningfully related to existing context; preserve history and store it as a separate fact.",
            confidence=0.88,
            original_text=new_memory_text,
            verbatim_request=new_memory_text,
            preserve_history=True,
            metadata={"source": "heuristic", "relatedness_score": best_score},
        )

    contradictory_records = [
        record
        for record in relevant_context
        if _is_contradictory(new_memory_text, str(record.get("text") or ""))
    ]
    if contradictory_records:
        return ContextualMemoryDecision(
            decision=MemoryDecisionType.CORRECT,
            affected_memory_ids=[str(record.get("id")) for record in contradictory_records if record.get("id")],
            proposed_content=new_memory_text,
            reasoning_summary="The new memory contradicts prior facts; preserve the historical record and add the corrected fact as a separate memory.",
            confidence=0.94,
            original_text=new_memory_text,
            verbatim_request=new_memory_text,
            preserve_history=True,
            metadata={"source": "heuristic", "relatedness_score": best_score},
        )

    if _is_history_update(new_memory_text, str(best_match.get("text") or "")):
        return ContextualMemoryDecision(
            decision=MemoryDecisionType.UPDATE,
            affected_memory_ids=[str(best_match.get("id"))] if best_match and best_match.get("id") else [],
            proposed_content=new_memory_text,
            reasoning_summary="The new memory captures a time-sensitive update to an existing fact while preserving the historical context.",
            confidence=0.92,
            original_text=new_memory_text,
            verbatim_request=new_memory_text,
            preserve_history=True,
            metadata={"source": "heuristic", "relatedness_score": best_score},
        )

    if best_score >= 0.25:
        return ContextualMemoryDecision(
            decision=MemoryDecisionType.ENRICH,
            affected_memory_ids=[str(best_match.get("id"))] if best_match and best_match.get("id") else [],
            proposed_content=new_memory_text,
            reasoning_summary="The new memory enriches an existing related fact without contradicting it.",
            confidence=0.86,
            original_text=new_memory_text,
            verbatim_request=new_memory_text,
            preserve_history=False,
            metadata={"source": "heuristic", "relatedness_score": best_score},
        )

    return ContextualMemoryDecision(
        decision=MemoryDecisionType.CREATE,
        affected_memory_ids=[],
        proposed_content=new_memory_text,
        reasoning_summary="The memory appears distinct and should be preserved as a fresh fact.",
        confidence=0.79,
        original_text=new_memory_text,
        verbatim_request=new_memory_text,
        preserve_history=False,
        metadata={"source": "heuristic", "relatedness_score": best_score},
    )


def _safe_create_decision(*, new_memory_text: str, reason: str) -> ContextualMemoryDecision:
    return ContextualMemoryDecision(
        decision=MemoryDecisionType.CREATE,
        affected_memory_ids=[],
        proposed_content=new_memory_text,
        reasoning_summary=reason,
        confidence=0.65,
        original_text=new_memory_text,
        verbatim_request=new_memory_text,
        preserve_history=False,
        metadata={"source": "safe_create_fallback"},
    )


async def decide_contextual_memory_action(
    *,
    user_id: str,
    new_memory_text: str,
    relevant_context: Sequence[Mapping[str, Any] | MemorySyncRecord],
    gemini_adapter: GeminiAdapter | None = None,
) -> ContextualMemoryDecision:
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("A non-empty user_id is required.")

    text = str(new_memory_text).strip()
    if not text:
        raise ValueError("Memory text cannot be empty.")

    if gemini_adapter is None:
        return _safe_create_decision(
            new_memory_text=text,
            reason="Gemini is unavailable; preserve the new memory as a standalone record instead of inferring an update.",
        )

    serialized_context = [_serialize_memory_record(item) for item in relevant_context]
    decision = await _decide_with_gemini(
        new_memory_text=text,
        relevant_context=serialized_context,
        adapter=gemini_adapter,
    )
    if decision is not None:
        return decision

    return _safe_create_decision(
        new_memory_text=text,
        reason="Gemini did not return a valid decision; preserve the new memory as a standalone record.",
    )


async def persist_contextual_memory_decision(
    session,
    walrus,
    *,
    user_id: str,
    decision: ContextualMemoryDecision,
    idempotency_key: str | None = None,
) -> MemorySyncRecord:
    if not isinstance(decision, ContextualMemoryDecision):
        raise TypeError("A ContextualMemoryDecision instance is required.")

    proposed_text = decision.proposed_content or decision.original_text
    metadata: dict[str, Any] = {
        "source": "contextual_memory_intelligence",
        "decision": decision.decision.value,
        "reasoning_summary": decision.reasoning_summary,
        "confidence": float(decision.confidence),
        "affected_memory_ids": decision.affected_memory_ids,
        "preserve_history": decision.preserve_history,
        "original_text": decision.original_text,
        "verbatim_request": decision.verbatim_request or decision.original_text,
    }
    metadata.update(decision.metadata)

    seed = (
        f"{user_id}\0{decision.decision.value}\0{proposed_text}\0{decision.original_text}\0"
        f"{decision.verbatim_request or ''}"
    )
    request_key = idempotency_key or hashlib.sha256(seed.encode("utf-8")).hexdigest()

    lock = _get_user_lock(user_id)
    async with lock:
        from app.services.memory_persistence import persist_memory

        record = await persist_memory(
            session,
            walrus,
            user_id=user_id,
            text=proposed_text,
            category="personal",
            memory_type="semantic",
            metadata=metadata,
            request_idempotency_key=request_key,
            source="contextual_memory_intelligence",
            confidence=float(decision.confidence),
        )

    decision.memory_id = record.id
    return record


async def apply_contextual_memory_decision(
    session,
    walrus,
    *,
    user_id: str,
    new_memory_text: str,
    relevant_context: Sequence[Mapping[str, Any] | MemorySyncRecord],
    gemini_adapter: GeminiAdapter | None = None,
) -> ContextualMemoryDecision:
    decision = await decide_contextual_memory_action(
        user_id=user_id,
        new_memory_text=new_memory_text,
        relevant_context=relevant_context,
        gemini_adapter=gemini_adapter,
    )
    if walrus is not None:
        await persist_contextual_memory_decision(
            session,
            walrus,
            user_id=user_id,
            decision=decision,
        )
    return decision


__all__ = [
    "ContextualMemoryDecision",
    "MemoryDecisionType",
    "apply_contextual_memory_decision",
    "decide_contextual_memory_action",
    "persist_contextual_memory_decision",
]
