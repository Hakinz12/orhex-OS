"""Tests for the Walrus SDK adapter using fake clients only."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.adapters.walrus import memwal
from app.adapters.walrus.memwal import MemWalAdapter, WalrusAdapterError
from app.core.config import settings


TEST_PRIVATE_KEY = "a" * 64
TEST_ACCOUNT_ID = "0x" + "b" * 64
TEST_SERVER_URL = "https://walrus.example.test/custom/relayer"
TEST_NAMESPACE = "orhrex-test"


@pytest.fixture(autouse=True)
def isolated_walrus_settings(monkeypatch):
    """Keep these tests independent of the developer's local .env file."""
    monkeypatch.setattr(settings, "MEMWAL_PRIVATE_KEY", TEST_PRIVATE_KEY)
    monkeypatch.setattr(settings, "MEMWAL_ACCOUNT_ID", TEST_ACCOUNT_ID)
    monkeypatch.setattr(settings, "MEMWAL_SERVER_URL", TEST_SERVER_URL)
    monkeypatch.setattr(settings, "MEMWAL_NAMESPACE", TEST_NAMESPACE)
    monkeypatch.setattr(settings, "MEMWAL_AGENT_ID", "test-agent")
    monkeypatch.setattr(settings, "WALRUS_NETWORK", "mainnet")
    monkeypatch.setattr(memwal, "_client", None)
    memwal._managed_clients.clear()
    yield
    monkeypatch.setattr(memwal, "_client", None)
    memwal._managed_clients.clear()


@pytest.mark.asyncio
async def test_sdk_client_initializes_with_configured_server_url(monkeypatch):
    fake_client = SimpleNamespace(close=AsyncMock())
    create = Mock(return_value=fake_client)
    monkeypatch.setattr(memwal.MemWal, "create", create)

    client = await memwal.get_walrus_client()

    assert client is fake_client
    create.assert_called_once_with(
        key=TEST_PRIVATE_KEY,
        account_id=TEST_ACCOUNT_ID,
        server_url=TEST_SERVER_URL,
        namespace=TEST_NAMESPACE,
    )


@pytest.mark.asyncio
async def test_sdk_client_initialization_failure_is_reported(monkeypatch):
    create = Mock(side_effect=RuntimeError("fake initialization failure"))
    monkeypatch.setattr(memwal.MemWal, "create", create)

    result = await MemWalAdapter().check_readiness()

    assert result["status"] == "init_failed"
    assert result["error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_readiness_uses_sdk_health_check():
    fake_client = SimpleNamespace(
        health=AsyncMock(
            return_value=SimpleNamespace(
                status="ok",
                relayer_version="1.2.3",
                write_ready=True,
                mode="production",
            )
        )
    )
    result = await MemWalAdapter(client=fake_client).check_readiness()

    assert result["configured"] is True
    assert result["status"] == "ready"
    assert result["write_ready"] is True
    assert result["relayer_version"] == "1.2.3"
    assert result["server_url"] == "https://walrus.example.test"
    fake_client.health.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_readiness_reports_degraded_health():
    fake_client = SimpleNamespace(
        health=AsyncMock(
            return_value=SimpleNamespace(
                status="degraded",
                relayer_version="1.2.3",
                write_ready=False,
                mode="maintenance",
            )
        )
    )

    result = await MemWalAdapter(client=fake_client).check_readiness()

    assert result["status"] == "degraded"
    assert result["write_ready"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("private_key", "account_id"),
    [
        ("not-hex", TEST_ACCOUNT_ID),
        (TEST_PRIVATE_KEY, "0x1234"),
    ],
)
async def test_invalid_configuration_prevents_client_creation(
    monkeypatch, private_key, account_id
):
    create = Mock(side_effect=AssertionError("SDK client must not be created"))
    monkeypatch.setattr(memwal.MemWal, "create", create)
    adapter = MemWalAdapter(private_key=private_key, account_id=account_id)

    readiness = await adapter.check_readiness()
    write = await adapter.remember(namespace=TEST_NAMESPACE, text="valid text")

    assert readiness["configured"] is False
    assert readiness["status"] == "invalid_configuration"
    assert write["status"] == "failed"
    create.assert_not_called()


@pytest.mark.asyncio
async def test_readiness_reports_sdk_health_connection_failure():
    fake_client = SimpleNamespace(health=AsyncMock(side_effect=ConnectionError()))

    result = await MemWalAdapter(client=fake_client).check_readiness()

    assert result["status"] == "unreachable"
    assert result["error"] == "ConnectionError"


@pytest.mark.asyncio
async def test_confirmed_write_returns_sdk_blob_and_memory_ids():
    fake_client = SimpleNamespace(
        remember_and_wait=AsyncMock(
            return_value=SimpleNamespace(
                id="memory-123",
                blob_id="blob-456",
                owner="owner-789",
                namespace=TEST_NAMESPACE,
            )
        )
    )
    adapter = MemWalAdapter(client=fake_client)

    result = await adapter.remember(
        namespace=TEST_NAMESPACE,
        text="A verified memory",
        category="goals",
        memory_type="semantic",
        idempotency_key="persisted-operation-1",
    )

    assert result["status"] == "confirmed"
    assert result["blob_id"] == "blob-456"
    assert result["memory_id"] == "memory-123"
    assert result["namespace"] == TEST_NAMESPACE
    assert result["category"] == "goals"
    fake_client.remember_and_wait.assert_awaited_once_with(
        text="A verified memory",
        namespace=TEST_NAMESPACE,
        poll_interval_ms=1500,
        timeout_ms=90_000,
        idempotency_key="persisted-operation-1",
    )


@pytest.mark.asyncio
async def test_write_failure_is_returned_without_success_shape():
    fake_client = SimpleNamespace(
        remember_and_wait=AsyncMock(side_effect=RuntimeError("fake SDK failure"))
    )

    result = await MemWalAdapter(client=fake_client).remember(
        namespace=TEST_NAMESPACE,
        text="A memory that fails",
    )

    assert result["status"] == "failed"
    assert result["error_type"] == "RuntimeError"
    assert "blob_id" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (memwal.MemWalRememberJobTimeout("job-1", 90_000), "timeout"),
        (memwal.MemWalRateLimited(["job-1"]), "rate_limited"),
        (memwal.MemWalRememberJobFailed("job-1", "fake failure"), "failed"),
    ],
)
async def test_sdk_write_failures_keep_their_status(failure, expected_status):
    fake_client = SimpleNamespace(remember_and_wait=AsyncMock(side_effect=failure))

    result = await MemWalAdapter(client=fake_client).remember(
        namespace=TEST_NAMESPACE,
        text="A memory that fails",
    )

    assert result["status"] == expected_status
    assert "error_type" in result
    assert "blob_id" not in result


@pytest.mark.asyncio
async def test_timeout_preserves_job_id_for_reconciliation():
    fake_client = SimpleNamespace(
        remember_and_wait=AsyncMock(
            side_effect=memwal.MemWalRememberJobTimeout("job-uncertain", 90_000)
        )
    )

    result = await MemWalAdapter(client=fake_client).remember(
        namespace=TEST_NAMESPACE,
        text="A write with an uncertain result",
        idempotency_key="persisted-operation-2",
    )

    assert result["status"] == "timeout"
    assert result["job_id"] == "job-uncertain"


@pytest.mark.asyncio
async def test_remember_job_status_is_available_for_reconciliation():
    fake_client = SimpleNamespace(
        get_remember_status=AsyncMock(
            return_value=SimpleNamespace(
                job_id="job-123",
                status="done",
                blob_id="blob-456",
                namespace=TEST_NAMESPACE,
                error=None,
            )
        )
    )

    result = await MemWalAdapter(client=fake_client).get_remember_status("job-123")

    assert result == {
        "job_id": "job-123",
        "status": "done",
        "blob_id": "blob-456",
        "namespace": TEST_NAMESPACE,
        "error": None,
    }


@pytest.mark.asyncio
async def test_semantic_recall_maps_sdk_results():
    fake_client = SimpleNamespace(
        recall=AsyncMock(
            return_value=SimpleNamespace(
                results=[
                    SimpleNamespace(
                        blob_id="blob-123",
                        text="A recalled fact",
                        distance=0.15,
                        score=0.85,
                        created_at="2026-10-28T00:00:00Z",
                    )
                ]
            )
        )
    )

    results = await MemWalAdapter(client=fake_client).recall(
        namespace=TEST_NAMESPACE,
        query="the fact",
        limit=5,
    )

    assert results == [
        {
            "blob_id": "blob-123",
            "text": "A recalled fact",
            "distance": 0.15,
            "score": 0.85,
            "created_at": "2026-10-28T00:00:00Z",
            "namespace": TEST_NAMESPACE,
        }
    ]
    params = fake_client.recall.await_args.args[0]
    assert params.query == "the fact"
    assert params.limit == 5
    assert params.namespace == TEST_NAMESPACE


@pytest.mark.asyncio
async def test_recall_failure_is_surfaced():
    fake_client = SimpleNamespace(recall=AsyncMock(side_effect=ConnectionError()))

    with pytest.raises(WalrusAdapterError, match="recall failed"):
        await MemWalAdapter(client=fake_client).recall(
            namespace=TEST_NAMESPACE,
            query="the fact",
        )


@pytest.mark.asyncio
async def test_shutdown_closes_sdk_client_and_clears_singleton():
    fake_client = SimpleNamespace(close=AsyncMock())
    memwal._client = fake_client
    memwal._managed_clients.append(fake_client)

    await memwal.close_walrus_client()

    fake_client.close.assert_awaited_once_with()
    assert memwal._client is None


@pytest.mark.asyncio
async def test_shutdown_surfaces_sdk_close_failure_and_clears_singleton():
    fake_client = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError()))
    memwal._client = fake_client
    memwal._managed_clients.append(fake_client)

    with pytest.raises(RuntimeError):
        await memwal.close_walrus_client()

    assert memwal._client is None


@pytest.mark.asyncio
async def test_unsupported_lifecycle_operations_are_explicit():
    adapter = MemWalAdapter(client=SimpleNamespace())

    assert (await adapter.get_blob_status("blob-123"))["status"] == "unsupported"
    deletion = await adapter.delete_memory(TEST_NAMESPACE, "memory-123")
    assert deletion["success"] is False
    assert deletion["status"] == "unsupported"
