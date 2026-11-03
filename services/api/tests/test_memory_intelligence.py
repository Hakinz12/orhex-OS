import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.core.database import Base, _enable_sqlite_foreign_keys
from app.models.domain import MemorySyncRecord, User
from app.services.memory_intelligence import (
    MemoryDecisionType,
    decide_contextual_memory_action,
    persist_contextual_memory_decision,
    apply_contextual_memory_decision,
)
from app.services.memory_persistence import persist_memory
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class FakeGemini:
    async def check_readiness(self):
        return {"status": "ready", "model": "gemini-test"}

    async def generate_response(self, *args, **kwargs):
        payload = json.loads(kwargs.get("user_message", "{}") or "{}")
        new_memory = str(payload.get("new_memory", "")).lower()
        context_ids = [
            item["id"]
            for item in payload.get("relevant_context", [])
            if item.get("id") is not None
        ]

        if "coffee" in new_memory:
            return {
                "decision": "CORRECT",
                "affected_memory_ids": context_ids,
                "proposed_content": "I do not drink coffee anymore; I prefer tea.",
                "reasoning_summary": "This contradicts the prior coffee preference and preserves the historical fact.",
                "confidence": 0.97,
            }

        if "market" in new_memory or "closed" in new_memory or "french" in new_memory:
            return {
                "decision": "PRESERVE_HISTORY",
                "affected_memory_ids": context_ids,
                "proposed_content": new_memory.capitalize(),
                "reasoning_summary": "This is unrelated to the prior memory; preserve the historical context and store a separate fact.",
                "confidence": 0.9,
            }

        return {
            "decision": "UPDATE",
            "affected_memory_ids": context_ids,
            "proposed_content": "I now live in Boston.",
            "reasoning_summary": "This updates the residence to Boston while preserving the prior fact.",
            "confidence": 0.96,
        }


class FakeWalrus:
    def __init__(self):
        self.remember = AsyncMock(return_value={"status": "confirmed", "blob_id": "blob-1", "memory_id": "mem-1", "namespace": "orhex-personal-memory"})


@pytest.fixture
async def intelligence_db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def add_user(session, user_id="owner-1"):
    user = User(id=user_id, preferred_name="Test Owner")
    session.add(user)
    await session.commit()


@pytest.mark.asyncio
async def test_semantically_related_memory_uses_contextual_update(intelligence_db_session):
    decision = await decide_contextual_memory_action(
        user_id="owner-1",
        new_memory_text="I now live in Boston.",
        relevant_context=[{
            "id": "existing-1",
            "text": "I live in Seattle and work from home.",
            "category": "personal",
            "memory_type": "semantic",
            "status": "confirmed",
        }],
        gemini_adapter=FakeGemini(),
    )

    assert decision.decision == MemoryDecisionType.UPDATE
    assert decision.affected_memory_ids == ["existing-1"]
    assert decision.preserve_history is True
    assert "Boston" in decision.proposed_content
    assert decision.confidence >= 0.9


@pytest.mark.asyncio
async def test_historical_change_keeps_prior_memory_intact(intelligence_db_session):
    await add_user(intelligence_db_session, user_id="owner-1")
    walrus = FakeWalrus()

    original = await persist_memory(
        intelligence_db_session,
        walrus,
        user_id="owner-1",
        text="I used to live in Seattle.",
        category="personal",
        memory_type="semantic",
        metadata={"history": True},
    )

    decision = await decide_contextual_memory_action(
        user_id="owner-1",
        new_memory_text="I moved to Boston in June.",
        relevant_context=[{
            "id": original.id,
            "text": "I used to live in Seattle.",
            "category": "personal",
            "memory_type": "semantic",
            "status": "confirmed",
            "metadata": {"history": True},
        }],
        gemini_adapter=FakeGemini(),
    )

    assert decision.decision == MemoryDecisionType.UPDATE
    assert decision.preserve_history is True
    assert "Boston" in decision.proposed_content
    updated = await persist_contextual_memory_decision(
        intelligence_db_session,
        walrus,
        user_id="owner-1",
        decision=decision,
        idempotency_key="historical-change",
    )
    stored_original = await intelligence_db_session.get(MemorySyncRecord, original.id)
    assert updated.id != original.id
    assert updated.text == decision.proposed_content
    assert stored_original.text == "I used to live in Seattle."


@pytest.mark.asyncio
async def test_contradictory_fact_is_marked_as_correction(intelligence_db_session):
    await add_user(intelligence_db_session, user_id="owner-1")
    walrus = FakeWalrus()
    original = await persist_memory(
        intelligence_db_session,
        walrus,
        user_id="owner-1",
        text="I love coffee in the morning.",
        category="personal",
        memory_type="semantic",
    )
    decision = await decide_contextual_memory_action(
        user_id="owner-1",
        new_memory_text="Actually, I do not drink coffee anymore; I prefer tea.",
        relevant_context=[{
            "id": original.id,
            "text": "I love coffee in the morning.",
            "category": "personal",
            "memory_type": "semantic",
            "status": "confirmed",
        }],
        gemini_adapter=FakeGemini(),
    )

    assert decision.decision == MemoryDecisionType.CORRECT
    assert decision.preserve_history is True
    assert decision.affected_memory_ids == [original.id]
    corrected = await persist_contextual_memory_decision(
        intelligence_db_session,
        walrus,
        user_id="owner-1",
        decision=decision,
        idempotency_key="coffee-correction",
    )
    stored_original = await intelligence_db_session.get(MemorySyncRecord, original.id)
    assert corrected.id != original.id
    assert corrected.text == decision.proposed_content
    assert stored_original.text == "I love coffee in the morning."


@pytest.mark.asyncio
async def test_unrelated_memory_is_preserved_without_overwrite(intelligence_db_session):
    await add_user(intelligence_db_session, user_id="owner-1")
    walrus = FakeWalrus()
    original = await persist_memory(
        intelligence_db_session,
        walrus,
        user_id="owner-1",
        text="I am learning French for a trip to Paris.",
        category="personal",
        memory_type="semantic",
    )
    decision = await decide_contextual_memory_action(
        user_id="owner-1",
        new_memory_text="The market closed up 2% today.",
        relevant_context=[{
            "id": original.id,
            "text": "I am learning French for a trip to Paris.",
            "category": "personal",
            "memory_type": "semantic",
            "status": "confirmed",
        }],
        gemini_adapter=FakeGemini(),
    )

    assert decision.decision == MemoryDecisionType.PRESERVE_HISTORY
    assert decision.preserve_history is True
    assert decision.affected_memory_ids == [original.id]
    separate = await persist_contextual_memory_decision(
        intelligence_db_session,
        walrus,
        user_id="owner-1",
        decision=decision,
        idempotency_key="unrelated-market-fact",
    )
    stored_original = await intelligence_db_session.get(MemorySyncRecord, original.id)
    assert separate.id != original.id
    assert separate.text == decision.proposed_content
    assert stored_original.text == "I am learning French for a trip to Paris."


@pytest.mark.asyncio
async def test_gemini_unavailable_falls_back_to_create(intelligence_db_session):
    decision = await decide_contextual_memory_action(
        user_id="owner-1",
        new_memory_text="I am teaching myself piano.",
        relevant_context=[],
        gemini_adapter=None,
    )

    assert decision.decision == MemoryDecisionType.CREATE
    assert "standalone" in decision.reasoning_summary.lower()


@pytest.mark.asyncio
async def test_unknown_affected_memory_id_fails_closed_to_create(intelligence_db_session):
    class InvalidReferenceGemini(FakeGemini):
        async def generate_response(self, *args, **kwargs):
            return {
                "decision": "UPDATE",
                "affected_memory_ids": ["not-in-context"],
                "proposed_content": "Changed content",
                "reasoning_summary": "The reference is invalid.",
                "confidence": 0.99,
            }

    decision = await decide_contextual_memory_action(
        user_id="owner-1",
        new_memory_text="A new fact.",
        relevant_context=[{"id": "owned-memory", "text": "An existing fact."}],
        gemini_adapter=InvalidReferenceGemini(),
    )

    assert decision.decision == MemoryDecisionType.CREATE
    assert decision.affected_memory_ids == []
    assert decision.original_text == "A new fact."


@pytest.mark.asyncio
async def test_concurrent_contextual_writes_share_one_idempotent_record(intelligence_db_session):
    await add_user(intelligence_db_session, user_id="owner-1")
    walrus = FakeWalrus()
    decision = {
        "decision": MemoryDecisionType.ENRICH,
        "affected_memory_ids": [],
        "proposed_content": "I prefer to review my goals every Sunday morning.",
        "reasoning_summary": "This enriches the user's weekly planning routine.",
        "confidence": 0.88,
        "original_text": "I prefer to review my goals every Sunday morning.",
        "verbatim_request": "I prefer to review my goals every Sunday morning.",
        "metadata": {"source": "test"},
    }

    async def persist_once():
        return await persist_contextual_memory_decision(
            intelligence_db_session,
            walrus,
            user_id="owner-1",
            decision=type("Decision", (), decision)(),
            idempotency_key="same-contextual-key",
        )

    # Build a real model instance for the persisted decision.
    real_decision = type("Decision", (), {})()
    for key, value in decision.items():
        setattr(real_decision, key, value)
    real_decision = type("DecisionModel", (object,), decision)() if False else None

    # Recreate with the actual Pydantic model to keep the contract valid.
    from app.services.memory_intelligence import ContextualMemoryDecision

    model_decision = ContextualMemoryDecision(**decision)

    async def persist_model():
        return await persist_contextual_memory_decision(
            intelligence_db_session,
            walrus,
            user_id="owner-1",
            decision=model_decision,
            idempotency_key="same-contextual-key",
        )

    first, second = await asyncio.gather(persist_model(), persist_model())
    assert first.id == second.id
    assert walrus.remember.await_count == 1


@pytest.mark.asyncio
async def test_apply_contextual_memory_decision_persists_without_overwriting(intelligence_db_session):
    await add_user(intelligence_db_session, user_id="owner-1")
    walrus = FakeWalrus()
    await persist_memory(
        intelligence_db_session,
        walrus,
        user_id="owner-1",
        text="I am currently a product manager.",
        category="personal",
        memory_type="semantic",
        metadata={"role": "product manager"},
    )

    decision = await apply_contextual_memory_decision(
        intelligence_db_session,
        walrus,
        user_id="owner-1",
        new_memory_text="I am now a senior product manager at a startup.",
        relevant_context=[{
            "id": "role-1",
            "text": "I am currently a product manager.",
            "category": "personal",
            "memory_type": "semantic",
            "status": "confirmed",
        }],
        gemini_adapter=None,
    )

    assert decision.decision == MemoryDecisionType.CREATE
    assert decision.memory_id is not None
    stored = await intelligence_db_session.get(MemorySyncRecord, decision.memory_id)
    assert stored is not None
    assert stored.text == "I am now a senior product manager at a startup."
    assert stored.metadata_json is not None
