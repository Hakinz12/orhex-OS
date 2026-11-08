import asyncio
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.llm.gemini import (
    GeminiAuthenticationError,
    GeminiModelUnavailableError,
    GeminiProviderError,
    GeminiRateLimitError,
    GeminiResponseError,
    GeminiTimeoutError,
    GeminiUnavailableError,
)
from app.api.v1 import conversations as conversations_api
from app.core.auth import provision_user
from app.core.database import Base, _enable_sqlite_foreign_keys, get_db
from app.core.rate_limit import SensitiveEndpointRateLimiter
from app.main import app
from app.models.domain import (
    Conversation,
    ConversationMessage,
    MemorySyncRecord,
    utc_now,
)
from app.services.anonymous_visitor_identity import provision_visitor_identity
from app.services.memory_persistence import namespace_for_user


class FakeGemini:
    def __init__(self):
        self.calls = []
        self.memory_contexts = []
        self.wallet_contexts = []
        self.failure = None
        self.concurrent_barrier = None
        self.concurrent_count = 0
        self.concurrent_lock = asyncio.Lock()

    async def generate_conversation_response(
        self, messages, memory_context=None, wallet_context=None
    ):
        self.calls.append(messages)
        self.memory_contexts.append(memory_context)
        self.wallet_contexts.append(wallet_context)
        if self.failure is not None:
            raise self.failure
        if self.concurrent_barrier is not None and messages[-1]["content"].startswith("parallel-"):
            async with self.concurrent_lock:
                self.concurrent_count += 1
                if self.concurrent_count == 2:
                    self.concurrent_barrier.set()
            await self.concurrent_barrier.wait()
        return f"Reply to: {messages[-1]['content']}"


class FakeWalrus:
    def __init__(self):
        self.calls = []
        self.results = {}
        self.failure = None

    async def recall(self, *, namespace, query, limit=5):
        self.calls.append({"namespace": namespace, "query": query, "limit": limit})
        if self.failure is not None:
            raise self.failure
        return self.results.get(namespace, [])


@pytest.fixture
async def chat_harness(monkeypatch, tmp_path):
    database_path = str(tmp_path / "conversation-tests.db").replace("\\", "/")
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake_gemini = FakeGemini()
    fake_walrus = FakeWalrus()
    monkeypatch.setattr(conversations_api, "_gemini", fake_gemini)
    monkeypatch.setattr(conversations_api, "_walrus", fake_walrus)

    from app import main as main_module

    monkeypatch.setattr(
        main_module,
        "_sensitive_rate_limiter",
        SensitiveEndpointRateLimiter(requests_per_minute=500),
    )

    async def override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield SimpleNamespace(
                client=client,
                factory=factory,
                gemini=fake_gemini,
                walrus=fake_walrus,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


async def provision_visitor(harness):
    async with harness.factory() as session:
        return await provision_visitor_identity(session)


async def provision_pat(harness):
    async with harness.factory() as session:
        return await provision_user(session)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def chat_body(message, *, conversation_id=None, idempotency_key="11111111-1111-4111-8111-111111111111"):
    body = {"message": message, "idempotency_key": idempotency_key}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    return body


@pytest.mark.asyncio
async def test_explicit_wallet_context_opt_in_passes_verified_visitor_context_to_gemini(
    chat_harness,
    monkeypatch,
):
    from app.models.schemas import SuiCoinBalance
    from app.services.wallet_context import WalletContext

    visitor = await provision_visitor(chat_harness)
    observed = []

    async def retrieve_wallet_context(db, *, user_id, visitor_identity_id):
        observed.append((user_id, visitor_identity_id))
        return WalletContext(
            status="retrieved",
            address="0x" + "a" * 64,
            network="mainnet",
            native_balance=SuiCoinBalance(
                coin_type="0x2::sui::SUI",
                raw_balance="2500000000",
                amount="2.5",
                decimals=9,
                symbol="SUI",
            ),
        )

    monkeypatch.setattr(
        conversations_api,
        "retrieve_visitor_wallet_context",
        retrieve_wallet_context,
    )
    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json={
            **chat_body("How much SUI do I have?"),
            "include_wallet_context": True,
        },
        headers=bearer(visitor.credential),
    )

    assert response.status_code == 200
    assert observed == [(visitor.user.id, visitor.identity.id)]
    context = chat_harness.gemini.wallet_contexts[0]
    assert context.status == "retrieved"
    assert context.address == "0x" + "a" * 64
    assert context.native_balance.amount == "2.5"


@pytest.mark.asyncio
@pytest.mark.parametrize("include_wallet_context", [None, False])
async def test_wallet_context_is_off_by_default_and_not_retrieved(
    chat_harness,
    monkeypatch,
    include_wallet_context,
):
    visitor = await provision_visitor(chat_harness)
    calls = []

    async def unexpected_wallet_lookup(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Wallet context must not be retrieved without opt-in")

    monkeypatch.setattr(
        conversations_api,
        "retrieve_visitor_wallet_context",
        unexpected_wallet_lookup,
    )
    body = chat_body(
        "Hello without wallet context",
        idempotency_key="51515151-5151-4151-8151-515151515151",
    )
    if include_wallet_context is not None:
        body["include_wallet_context"] = include_wallet_context
    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json=body,
        headers=bearer(visitor.credential),
    )

    assert response.status_code == 200
    assert calls == []
    assert chat_harness.gemini.wallet_contexts[0] is None


@pytest.mark.asyncio
async def test_opted_in_invalid_wallet_association_is_not_sent_to_gemini(
    chat_harness,
    monkeypatch,
):
    from datetime import datetime, timezone

    from app.core.config import settings
    from app.models.domain import WalletAssociation

    monkeypatch.setattr(settings, "SUI_NETWORK", "mainnet")
    visitor = await provision_visitor(chat_harness)
    async with chat_harness.factory() as db:
        db.add(WalletAssociation(
            visitor_identity_id=visitor.identity.id,
            user_id=visitor.user.id,
            chain="sui",
            network="testnet",
            canonical_address="0x" + "a" * 64,
            verified_at=datetime.now(timezone.utc),
        ))
        await db.commit()

    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json={
            **chat_body(
                "Do not use an invalid wallet association",
                idempotency_key="52525252-5252-4252-8252-525252525252",
            ),
            "include_wallet_context": True,
        },
        headers=bearer(visitor.credential),
    )

    assert response.status_code == 200
    assert chat_harness.gemini.wallet_contexts[0] is None
    assert "0x" + "a" * 64 not in str(chat_harness.gemini.calls[0])


@pytest.mark.asyncio
async def test_opted_in_unverified_wallet_is_not_sent_to_gemini(chat_harness):
    visitor = await provision_visitor(chat_harness)
    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json={
            **chat_body(
                "No verified wallet is linked",
                idempotency_key="54545454-5454-4454-8454-545454545454",
            ),
            "include_wallet_context": True,
        },
        headers=bearer(visitor.credential),
    )

    assert response.status_code == 200
    assert chat_harness.gemini.wallet_contexts[0] is None


@pytest.mark.asyncio
async def test_opted_in_wallet_lookup_cannot_cross_visitor_ownership(
    chat_harness,
    monkeypatch,
):
    from datetime import datetime, timezone

    from app.core.config import settings
    from app.models.domain import WalletAssociation

    monkeypatch.setattr(settings, "SUI_NETWORK", "mainnet")
    wallet_owner = await provision_visitor(chat_harness)
    request_owner = await provision_visitor(chat_harness)
    async with chat_harness.factory() as db:
        db.add(WalletAssociation(
            visitor_identity_id=wallet_owner.identity.id,
            user_id=wallet_owner.user.id,
            chain="sui",
            network="mainnet",
            canonical_address="0x" + "a" * 64,
            verified_at=datetime.now(timezone.utc),
        ))
        await db.commit()

    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json={
            **chat_body(
                "Do not use another visitor's wallet",
                idempotency_key="53535353-5353-4353-8353-535353535353",
            ),
            "include_wallet_context": True,
        },
        headers=bearer(request_owner.credential),
    )

    assert response.status_code == 200
    assert chat_harness.gemini.wallet_contexts[0] is None
    assert "0x" + "a" * 64 not in str(chat_harness.gemini.calls[0])


@pytest.mark.asyncio
async def test_wallet_context_opt_in_requires_a_boolean(chat_harness):
    visitor = await provision_visitor(chat_harness)
    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json={
            **chat_body(
                "Invalid wallet context consent",
                idempotency_key="55555555-5555-4555-8555-555555555555",
            ),
            "include_wallet_context": "true",
        },
        headers=bearer(visitor.credential),
    )

    assert response.status_code == 422
    assert chat_harness.gemini.calls == []


@pytest.mark.asyncio
async def test_pat_conversation_never_attempts_to_resolve_visitor_wallet_context(
    chat_harness,
    monkeypatch,
):
    _, pat_token = await provision_pat(chat_harness)
    calls = []

    async def unexpected_wallet_lookup(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("PAT conversations must not resolve visitor wallets")

    monkeypatch.setattr(
        conversations_api,
        "retrieve_visitor_wallet_context",
        unexpected_wallet_lookup,
    )
    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body(
            "PAT conversation without visitor wallet",
            idempotency_key="44444444-4444-4444-8444-444444444444",
        ),
        headers=bearer(pat_token),
    )

    assert response.status_code == 200
    assert calls == []
    assert chat_harness.gemini.wallet_contexts[0] is None


@pytest.mark.asyncio
async def test_visitor_conversation_exchange_and_reload_continuity(chat_harness):
    visitor = await provision_visitor(chat_harness)
    first = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("Hello, ORHEX."),
        headers=bearer(visitor.credential),
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["version"] == 1
    assert [item["role"] for item in first_payload["messages"]] == ["user", "assistant"]
    assert first_payload["messages"][1]["content"] == "Reply to: Hello, ORHEX."

    second = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body(
            "What did I just say?",
            conversation_id=first_payload["conversation_id"],
            idempotency_key="22222222-2222-4222-8222-222222222222",
        ),
        headers=bearer(visitor.credential),
    )
    assert second.status_code == 200
    assert second.json()["version"] == 2
    assert [item["content"] for item in chat_harness.gemini.calls[1]] == [
        "Hello, ORHEX.",
        "Reply to: Hello, ORHEX.",
        "What did I just say?",
    ]
    assert chat_harness.gemini.memory_contexts[0].status == "empty"
    assert first_payload["memory_context"] == {"status": "empty", "count": 0}

    loaded = await chat_harness.client.get(
        f"/api/v1/conversations/{first_payload['conversation_id']}",
        headers=bearer(visitor.credential),
    )
    assert loaded.status_code == 200
    assert loaded.json() == second.json()
    async with chat_harness.factory() as session:
        assert await session.scalar(select(func.count(MemorySyncRecord.id))) == 0


@pytest.mark.asyncio
async def test_conversation_authentication_and_owner_scopes_are_separate(chat_harness):
    first_visitor = await provision_visitor(chat_harness)
    second_visitor = await provision_visitor(chat_harness)
    pat_user, pat_token = await provision_pat(chat_harness)

    assert (await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("No credential"),
    )).status_code == 401

    visitor_response = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("Visitor conversation"),
        headers=bearer(first_visitor.credential),
    )
    pat_response = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body(
            "PAT conversation",
            idempotency_key="33333333-3333-4333-8333-333333333333",
        ),
        headers=bearer(pat_token),
    )
    assert visitor_response.status_code == 200
    assert pat_response.status_code == 200
    assert pat_user.id

    visitor_id = visitor_response.json()["conversation_id"]
    pat_id = pat_response.json()["conversation_id"]
    assert (await chat_harness.client.get(
        f"/api/v1/conversations/{visitor_id}",
        headers=bearer(second_visitor.credential),
    )).status_code == 404
    assert (await chat_harness.client.get(
        f"/api/v1/conversations/{visitor_id}",
        headers=bearer(pat_token),
    )).status_code == 404
    assert (await chat_harness.client.get(
        f"/api/v1/conversations/{pat_id}",
        headers=bearer(first_visitor.credential),
    )).status_code == 404


@pytest.mark.asyncio
async def test_conversation_rejects_oversized_and_client_owned_fields(chat_harness):
    visitor = await provision_visitor(chat_harness)
    oversized = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("x" * 4001),
        headers=bearer(visitor.credential),
    )
    ownership_override = await chat_harness.client.post(
        "/api/v1/conversations",
        json={
            **chat_body("Do not accept client ownership"),
            "user_id": "someone-else",
            "visitor_id": "someone-else",
        },
        headers=bearer(visitor.credential),
    )

    assert oversized.status_code == 422
    assert ownership_override.status_code == 422
    assert chat_harness.gemini.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (GeminiUnavailableError("private internal detail"), 503),
        (GeminiTimeoutError("private internal detail"), 504),
        (GeminiProviderError("private internal detail"), 503),
        (GeminiAuthenticationError("private internal detail"), 503),
        (GeminiModelUnavailableError("private internal detail"), 503),
        (GeminiRateLimitError("private internal detail"), 429),
        (GeminiResponseError("private internal detail"), 502),
    ],
)
async def test_provider_failures_are_safe_and_not_persisted(chat_harness, failure, status):
    visitor = await provision_visitor(chat_harness)
    chat_harness.gemini.failure = failure
    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("Do not fabricate an answer"),
        headers=bearer(visitor.credential),
    )

    assert response.status_code == status
    assert "private internal detail" not in response.text
    async with chat_harness.factory() as session:
        assert await session.scalar(select(func.count(Conversation.id))) == 0
        assert await session.scalar(select(func.count(ConversationMessage.id))) == 0


@pytest.mark.asyncio
async def test_idempotent_retry_does_not_duplicate_a_conversation_exchange(chat_harness):
    visitor = await provision_visitor(chat_harness)
    body = chat_body("Retry safely.")
    first = await chat_harness.client.post(
        "/api/v1/conversations",
        json=body,
        headers=bearer(visitor.credential),
    )
    retry = await chat_harness.client.post(
        "/api/v1/conversations",
        json=body,
        headers=bearer(visitor.credential),
    )

    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json()
    assert len(chat_harness.gemini.calls) == 1
    async with chat_harness.factory() as session:
        assert await session.scalar(select(func.count(Conversation.id))) == 1
        assert await session.scalar(select(func.count(ConversationMessage.id))) == 2


@pytest.mark.asyncio
async def test_context_is_bounded_by_message_count_and_character_budget(chat_harness):
    visitor = await provision_visitor(chat_harness)
    conversation_id = None
    for index in range(8):
        response = await chat_harness.client.post(
            "/api/v1/conversations",
            json=chat_body(
                f"{index} " + ("x" * 3998),
                conversation_id=conversation_id,
                idempotency_key=f"55555555-5555-4555-8555-{index:012d}",
            ),
            headers=bearer(visitor.credential),
        )
        assert response.status_code == 200
        conversation_id = response.json()["conversation_id"]

    context = chat_harness.gemini.calls[-1]
    assert len(context) <= 21
    assert sum(len(item["content"]) for item in context) <= 24_000
    assert context[0]["role"] == "user"
    assert context[-1]["role"] == "user"


@pytest.mark.asyncio
async def test_recall_uses_only_authenticated_visitor_namespace_and_passes_context(chat_harness):
    first_visitor = await provision_visitor(chat_harness)
    second_visitor = await provision_visitor(chat_harness)
    first_namespace = namespace_for_user(first_visitor.user.id)
    second_namespace = namespace_for_user(second_visitor.user.id)
    chat_harness.walrus.results = {
        first_namespace: [{
            "blob_id": "first-private-id",
            "text": "My preferred name is Sam.",
            "score": 0.91,
            "distance": 0.09,
            "created_at": "2025-01-02T03:04:05Z",
            "namespace": first_namespace,
        }],
        second_namespace: [{
            "blob_id": "second-private-id",
            "text": "My preferred name is Alex.",
            "namespace": second_namespace,
        }],
    }

    first_response = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("What name do I prefer?"),
        headers=bearer(first_visitor.credential),
    )
    second_response = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body(
            "What name do I prefer?",
            idempotency_key="88888888-8888-4888-8888-888888888888",
        ),
        headers=bearer(second_visitor.credential),
    )

    assert first_response.status_code == second_response.status_code == 200
    assert [call["namespace"] for call in chat_harness.walrus.calls] == [
        first_namespace,
        second_namespace,
    ]
    first_context, second_context = chat_harness.gemini.memory_contexts
    assert first_context.memories[0].content == "My preferred name is Sam."
    assert first_context.memories[0].memory_id == "first-private-id"
    assert first_context.memories[0].score == 0.91
    assert second_context.memories[0].content == "My preferred name is Alex."
    assert first_response.json()["memory_context"] == {"status": "retrieved", "count": 1}
    assert "first-private-id" not in first_response.text
    assert first_namespace not in first_response.text
    assert "second-private-id" not in second_response.text
    assert second_namespace not in second_response.text


@pytest.mark.asyncio
async def test_pat_memory_recall_is_owner_scoped(chat_harness):
    pat_user, pat_token = await provision_pat(chat_harness)
    visitor = await provision_visitor(chat_harness)
    pat_namespace = namespace_for_user(pat_user.id)
    visitor_namespace = namespace_for_user(visitor.user.id)
    chat_harness.walrus.results = {
        pat_namespace: [{
            "blob_id": "pat-private-id",
            "text": "I work in engineering.",
            "namespace": pat_namespace,
        }],
        visitor_namespace: [{
            "blob_id": "visitor-private-id",
            "text": "I work in design.",
            "namespace": visitor_namespace,
        }],
    }

    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("What is my profession?"),
        headers=bearer(pat_token),
    )

    assert response.status_code == 200
    assert chat_harness.walrus.calls[0]["namespace"] == pat_namespace
    assert chat_harness.gemini.memory_contexts[0].memories[0].content == "I work in engineering."
    assert "visitor-private-id" not in response.text


@pytest.mark.asyncio
async def test_recall_filters_trashed_memory_and_bounds_context(chat_harness):
    visitor = await provision_visitor(chat_harness)
    namespace = namespace_for_user(visitor.user.id)
    now = utc_now()
    async with chat_harness.factory() as session:
        session.add_all([
            MemorySyncRecord(
                user_id=visitor.user.id,
                namespace=namespace,
                idempotency_key="active-memory-key",
                walrus_blob_id="active-blob",
                text="Active memory",
                status="confirmed",
                created_at=now,
                updated_at=now,
                confirmed_at=now,
            ),
            MemorySyncRecord(
                user_id=visitor.user.id,
                namespace=namespace,
                idempotency_key="trashed-memory-key",
                walrus_blob_id="trashed-blob",
                text="Trashed memory",
                status="confirmed",
                created_at=now,
                updated_at=now,
                confirmed_at=now,
                trashed_at=now,
            ),
        ])
        await session.commit()
    chat_harness.walrus.results[namespace] = [
        {"blob_id": "active-blob", "text": "A" * 15_000},
        {"blob_id": "trashed-blob", "text": "Do not include this trashed fact."},
    ]

    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("Use my saved context."),
        headers=bearer(visitor.credential),
    )

    assert response.status_code == 200
    context = chat_harness.gemini.memory_contexts[0]
    assert context.status == "retrieved"
    assert context.count == 1
    assert len(context.memories[0].content) == 3000
    assert "Trashed memory" not in str(context.model_dump())
    assert response.json()["memory_context"] == {"status": "retrieved", "count": 1}


@pytest.mark.asyncio
async def test_memory_context_total_size_and_result_count_are_bounded(chat_harness):
    visitor = await provision_visitor(chat_harness)
    namespace = namespace_for_user(visitor.user.id)
    chat_harness.walrus.results[namespace] = [
        {"blob_id": f"memory-{index}", "text": "x" * 3000}
        for index in range(8)
    ]

    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("Use context."),
        headers=bearer(visitor.credential),
    )

    assert response.status_code == 200
    context = chat_harness.gemini.memory_contexts[0]
    assert chat_harness.walrus.calls[0]["limit"] == 5
    assert context.count == 4
    assert sum(len(memory.content) for memory in context.memories) == 10_000
    assert response.json()["memory_context"] == {"status": "retrieved", "count": 4}


@pytest.mark.asyncio
async def test_recall_failure_falls_back_without_memory_claim_metadata(chat_harness):
    visitor = await provision_visitor(chat_harness)
    chat_harness.walrus.failure = RuntimeError("private Walrus endpoint detail")

    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("Can you help me?"),
        headers=bearer(visitor.credential),
    )

    assert response.status_code == 200
    assert response.json()["messages"][-1]["content"] == "Reply to: Can you help me?"
    assert response.json()["memory_context"] == {"status": "unavailable", "count": 0}
    assert chat_harness.gemini.memory_contexts[0].status == "unavailable"
    assert "private Walrus endpoint detail" not in response.text


@pytest.mark.asyncio
async def test_recall_request_rejects_client_namespace_and_owner(chat_harness):
    visitor = await provision_visitor(chat_harness)

    response = await chat_harness.client.post(
        "/api/v1/conversations",
        json={
            **chat_body("Do not use client ownership."),
            "namespace": "shared-namespace",
            "user_id": "another-owner",
        },
        headers=bearer(visitor.credential),
    )

    assert response.status_code == 422
    assert chat_harness.walrus.calls == []


@pytest.mark.asyncio
async def test_concurrent_conversation_writes_use_version_compare_and_swap(chat_harness):
    visitor = await provision_visitor(chat_harness)
    created = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("Initial turn."),
        headers=bearer(visitor.credential),
    )
    conversation_id = created.json()["conversation_id"]
    chat_harness.gemini.concurrent_barrier = asyncio.Event()

    responses = await asyncio.gather(
        *[
            chat_harness.client.post(
                "/api/v1/conversations",
                json=chat_body(
                    f"parallel-{index}",
                    conversation_id=conversation_id,
                    idempotency_key=f"44444444-4444-4444-8444-{index:012d}",
                ),
                headers=bearer(visitor.credential),
            )
            for index in range(2)
        ]
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    loaded = await chat_harness.client.get(
        f"/api/v1/conversations/{conversation_id}",
        headers=bearer(visitor.credential),
    )
    assert loaded.status_code == 200
    assert loaded.json()["version"] == 2
    assert len(loaded.json()["messages"]) == 4


@pytest.mark.asyncio
async def test_concurrent_retries_with_same_key_persist_one_exchange(chat_harness):
    visitor = await provision_visitor(chat_harness)
    created = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("Initial turn."),
        headers=bearer(visitor.credential),
    )
    conversation_id = created.json()["conversation_id"]
    chat_harness.gemini.concurrent_barrier = asyncio.Event()
    retry_body = chat_body(
        "parallel-same",
        conversation_id=conversation_id,
        idempotency_key="77777777-7777-4777-8777-777777777777",
    )

    responses = await asyncio.gather(
        *[
            chat_harness.client.post(
                "/api/v1/conversations",
                json=retry_body,
                headers=bearer(visitor.credential),
            )
            for _ in range(2)
        ]
    )

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["version"] == 2
    async with chat_harness.factory() as session:
        assert await session.scalar(select(func.count(ConversationMessage.id))) == 4


@pytest.mark.asyncio
async def test_conversation_endpoint_uses_direct_peer_rate_limiter(chat_harness, monkeypatch):
    from app import main as main_module

    visitor = await provision_visitor(chat_harness)
    monkeypatch.setattr(
        main_module,
        "_sensitive_rate_limiter",
        SensitiveEndpointRateLimiter(requests_per_minute=1),
    )
    first = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body("First request."),
        headers=bearer(visitor.credential),
    )
    second = await chat_harness.client.post(
        "/api/v1/conversations",
        json=chat_body(
            "Second request.",
            idempotency_key="66666666-6666-4666-8666-666666666666",
        ),
        headers=bearer(visitor.credential),
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["retry-after"]
    assert len(chat_harness.gemini.calls) == 1
