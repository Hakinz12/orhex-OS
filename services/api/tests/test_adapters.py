"""Unit tests for decoupled provider adapters."""
import json

import httpx
import pytest
from app.adapters.llm.gemini import (
    GeminiAdapter,
    GeminiAuthenticationError,
    GeminiModelUnavailableError,
    GeminiProviderError,
    GeminiRateLimitError,
    GeminiResponseError,
    GeminiTimeoutError,
    GeminiUnavailableError,
)
from app.services.conversation_memory_context import (
    ConversationMemoryContext,
    RetrievedMemory,
)
from app.adapters.walrus.memwal import MemWalAdapter
from app.adapters.tatum.sui_rpc import TatumSuiAdapter
from app.core.config import settings
from app.models.schemas import SuiCoinBalance
from app.services.wallet_context import WalletContext


@pytest.mark.asyncio
async def test_gemini_adapter_unconfigured_behavior(monkeypatch):
    """Missing credentials must not create a success-shaped provider response."""
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "environment-key-is-not-used")
    adapter = GeminiAdapter(api_key="")
    readiness = await adapter.check_readiness()
    assert readiness["status"] == "unconfigured"
    assert "GEMINI_API_KEY" in readiness["message"]

    with pytest.raises(GeminiUnavailableError):
        await adapter.generate_response(
            system_prompt="Test",
            user_message="Hello",
            recalled_memories=[],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, GeminiAuthenticationError),
        (404, GeminiModelUnavailableError),
        (429, GeminiRateLimitError),
        (503, GeminiProviderError),
    ],
)
async def test_gemini_generation_surfaces_provider_status_without_fallback(
    monkeypatch, status, error
):
    client_factory = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(status, json={"provider_error": "private"})
    )
    monkeypatch.setattr(
        "app.adapters.llm.gemini.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )
    adapter = GeminiAdapter(api_key="unit-test-gemini-key")

    with pytest.raises(error):
        await adapter.generate_response(
            system_prompt="Test",
            user_message="Hello",
            recalled_memories=[],
        )


@pytest.mark.asyncio
async def test_gemini_generation_rejects_malformed_response_without_fallback(monkeypatch):
    client_factory = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "not-json"}]}}]},
        )
    )
    monkeypatch.setattr(
        "app.adapters.llm.gemini.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )
    adapter = GeminiAdapter(api_key="unit-test-gemini-key")

    with pytest.raises(GeminiResponseError):
        await adapter.generate_response(
            system_prompt="Test",
            user_message="Hello",
            recalled_memories=[],
        )


@pytest.mark.asyncio
async def test_gemini_readiness_uses_header_without_logging_key_in_url(monkeypatch):
    requests = []
    client_factory = httpx.AsyncClient

    def handle_request(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "displayName": "Gemini test model",
                "supportedGenerationMethods": ["generateContent"],
            },
        )

    transport = httpx.MockTransport(handle_request)
    monkeypatch.setattr(
        "app.adapters.llm.gemini.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )
    adapter = GeminiAdapter(api_key="unit-test-gemini-key")

    readiness = await adapter.check_readiness()

    assert readiness["status"] == "ready"
    assert len(requests) == 1
    assert requests[0].headers["x-goog-api-key"] == "unit-test-gemini-key"
    assert "unit-test-gemini-key" not in str(requests[0].url)


@pytest.mark.asyncio
async def test_gemini_generation_keeps_key_out_of_url(monkeypatch):
    requests = []
    client_factory = httpx.AsyncClient

    def handle_request(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        '{"answer":"ready","evidence":[],'
                                        '"recommendations":[]}'
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handle_request)
    monkeypatch.setattr(
        "app.adapters.llm.gemini.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )
    adapter = GeminiAdapter(api_key="unit-test-gemini-key")

    result = await adapter.generate_response(
        system_prompt="test system prompt",
        user_message="test user message",
        recalled_memories=[],
    )

    assert result["answer"] == "ready"
    assert len(requests) == 1
    assert requests[0].headers["x-goog-api-key"] == "unit-test-gemini-key"
    assert "unit-test-gemini-key" not in str(requests[0].url)
    assert "key" not in requests[0].url.params


@pytest.mark.asyncio
async def test_gemini_conversation_adapter_sends_turns_and_parses_validated_answer(monkeypatch):
    requests = []
    client_factory = httpx.AsyncClient

    def handle_request(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [{
                    "content": {
                        "parts": [{"text": '{"answer":"A real provider answer."}'}],
                    },
                }],
            },
        )

    transport = httpx.MockTransport(handle_request)
    monkeypatch.setattr(
        "app.adapters.llm.gemini.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )
    adapter = GeminiAdapter(api_key="unit-test-gemini-key")
    answer = await adapter.generate_conversation_response([
        {"role": "user", "content": "Earlier question"},
        {"role": "model", "content": "Earlier answer"},
        {"role": "user", "content": "Current question"},
    ], memory_context=ConversationMemoryContext(
        status="retrieved",
        memories=[
            RetrievedMemory(
                memory_id="private-memory-id",
                content="I prefer concise explanations.",
                score=0.88,
                created_at="2025-01-02T03:04:05Z",
            ),
            RetrievedMemory(
                memory_id="another-private-id",
                content="I prefer detailed explanations.",
                created_at="2024-01-02T03:04:05Z",
            ),
        ],
    ), wallet_context=WalletContext(
        status="retrieved",
        address="0x" + "a" * 64,
        network="mainnet",
        native_balance=SuiCoinBalance(
            coin_type="0x2::sui::SUI",
            raw_balance="1250000000",
            amount="1.25",
            decimals=9,
            symbol="SUI",
        ),
        token_balances=[
            SuiCoinBalance(
                coin_type=f"0x{index:x}::token::COIN",
                raw_balance="2500",
                amount="25",
                decimals=2,
                symbol="T" * 40,
            )
            for index in range(21)
        ],
    ))

    assert answer == "A real provider answer."
    assert len(requests) == 1
    assert requests[0].headers["x-goog-api-key"] == "unit-test-gemini-key"
    assert "unit-test-gemini-key" not in str(requests[0].url)
    payload = json.loads(requests[0].content)
    assert "systemInstruction" in payload
    assert [turn["role"] for turn in payload["contents"]] == ["user", "model", "user"]
    assert payload["contents"][-1]["parts"][0]["text"] == "Current question"
    memory_part = payload["contents"][-1]["parts"][1]["text"]
    assert "I prefer concise explanations." in memory_part
    assert "I prefer detailed explanations." in memory_part
    assert "private-memory-id" not in memory_part
    wallet_part = payload["contents"][-1]["parts"][2]["text"]
    assert "read-only snapshot" in wallet_part
    assert "1.25" in wallet_part
    assert "0x" + "a" * 64 in wallet_part
    assert "signing permission" in wallet_part
    wallet_json = json.loads(wallet_part.split("\n", 1)[1])
    assert len(wallet_json["token_balances"]) == 20
    assert wallet_json["has_more_token_balances"] is True
    assert len(wallet_json["token_balances"][0]["symbol"]) == 32
    assert "read-only snapshot" in payload["systemInstruction"]["parts"][0]["text"]
    assert "untrusted evidence" in payload["systemInstruction"]["parts"][0]["text"]
    assert "materially conflict" in payload["systemInstruction"]["parts"][0]["text"]


@pytest.mark.asyncio
async def test_gemini_conversation_adapter_fails_explicitly_without_credentials():
    adapter = GeminiAdapter(api_key="")
    with pytest.raises(GeminiUnavailableError):
        await adapter.generate_conversation_response([{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_gemini_conversation_adapter_rejects_malformed_provider_response(monkeypatch):
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "not-json"}]}}]},
        )
    )
    client_factory = httpx.AsyncClient
    monkeypatch.setattr(
        "app.adapters.llm.gemini.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )
    adapter = GeminiAdapter(api_key="unit-test-gemini-key")
    with pytest.raises(GeminiResponseError):
        await adapter.generate_conversation_response([{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_gemini_conversation_adapter_surfaces_provider_and_timeout_failures(monkeypatch):
    client_factory = httpx.AsyncClient
    status_transport = httpx.MockTransport(
        lambda _request: httpx.Response(503, json={"error": "details must stay private"})
    )
    monkeypatch.setattr(
        "app.adapters.llm.gemini.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=status_transport, **kwargs),
    )
    adapter = GeminiAdapter(api_key="unit-test-gemini-key")
    with pytest.raises(GeminiProviderError):
        await adapter.generate_conversation_response([{"role": "user", "content": "Hello"}])

    timeout_transport = httpx.MockTransport(
        lambda _request: (_ for _ in ()).throw(httpx.ReadTimeout("private"))
    )
    monkeypatch.setattr(
        "app.adapters.llm.gemini.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=timeout_transport, **kwargs),
    )
    with pytest.raises(GeminiTimeoutError):
        await adapter.generate_conversation_response([{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, GeminiAuthenticationError),
        (403, GeminiAuthenticationError),
        (404, GeminiModelUnavailableError),
        (429, GeminiRateLimitError),
    ],
)
async def test_gemini_conversation_provider_errors_are_classified(
    monkeypatch,
    status_code,
    error_type,
):
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            status_code,
            json={"error": "provider details must not surface to clients"},
        )
    )
    client_factory = httpx.AsyncClient
    monkeypatch.setattr(
        "app.adapters.llm.gemini.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )

    with pytest.raises(error_type):
        await GeminiAdapter(api_key="unit-test-gemini-key").generate_conversation_response(
            [{"role": "user", "content": "Hello"}]
        )


@pytest.mark.asyncio
async def test_walrus_adapter_unconfigured_behavior():
    """Verify Walrus adapter handles missing credentials and reports unconfigured."""
    adapter = MemWalAdapter(agent_id="", private_key="", account_id="")
    readiness = await adapter.check_readiness()
    assert readiness["configured"] is False
    assert readiness["agent_id"] == "not_set"

    # Memory persistence must fail safely if delegated credentials are missing.
    result = await adapter.remember(namespace="test_ns", text="Test memory")
    assert result["status"] == "failed"
    assert "MEMWAL_PRIVATE_KEY" in result["error"]


@pytest.mark.asyncio
async def test_tatum_sui_adapter_unconfigured_behavior(monkeypatch):
    """Verify unconfigured readiness without allowing any HTTP client creation."""
    monkeypatch.setenv("TATUM_API_KEY", "")
    monkeypatch.setenv("TATUM_SUI_RPC_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("SUI_NETWORK", "testnet")
    monkeypatch.setattr(settings, "TATUM_API_KEY", "")
    monkeypatch.setattr(settings, "TATUM_SUI_RPC_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(settings, "SUI_NETWORK", "testnet")

    def fail_on_http_request(*args, **kwargs):
        raise AssertionError("Unconfigured Tatum adapter attempted an HTTP request")

    monkeypatch.setattr(
        "app.adapters.tatum.sui_rpc.httpx.AsyncClient",
        fail_on_http_request,
    )
    adapter = TatumSuiAdapter()
    assert adapter.network == "testnet"
    assert adapter.rpc_url == "http://127.0.0.1:1"

    readiness = await adapter.check_readiness()
    assert readiness["configured"] is False
    assert readiness["status"] == "unconfigured"
    assert readiness["connected"] is False


@pytest.mark.asyncio
async def test_tatum_sui_readiness_uses_authenticated_graphql_checkpoint(monkeypatch):
    requests = []
    client_factory = httpx.AsyncClient

    def handle_request(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "chainIdentifier": "4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S",
                    "checkpoints": {"nodes": [{"sequenceNumber": "12345"}]},
                }
            },
        )

    transport = httpx.MockTransport(handle_request)
    monkeypatch.setattr(
        "app.adapters.tatum.sui_rpc.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )
    adapter = TatumSuiAdapter(
        api_key="unit-test-tatum-key",
        rpc_url="https://tatum.example",
        network="mainnet",
    )

    readiness = await adapter.check_readiness()

    assert readiness["status"] == "ready"
    assert readiness["connected"] is True
    assert readiness["latest_checkpoint"] == 12345
    assert len(requests) == 1
    assert requests[0].url.path == "/graphql"
    assert requests[0].headers["x-api-key"] == "unit-test-tatum-key"
    assert "unit-test-tatum-key" not in str(requests[0].url)
    assert "checkpoints(last: 1)" in requests[0].content.decode()


@pytest.mark.asyncio
async def test_tatum_sui_readiness_reports_gateway_and_graphql_failures(monkeypatch):
    client_factory = httpx.AsyncClient

    def fail_with_http_404(_request):
        return httpx.Response(404, json={"statusCode": 404, "message": "Not Found"})

    transport = httpx.MockTransport(fail_with_http_404)
    monkeypatch.setattr(
        "app.adapters.tatum.sui_rpc.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )
    adapter = TatumSuiAdapter(api_key="unit-test-tatum-key")

    readiness = await adapter.check_readiness()

    assert readiness["status"] == "unreachable"
    assert readiness["connected"] is False
    assert readiness["error"] == "http_404"
    assert "404" in readiness["message"]
    assert "unit-test-tatum-key" not in str(readiness)

    def fail_with_graphql_error(_request):
        return httpx.Response(
            200,
            json={"errors": [{"message": "Unsupported query"}], "data": None},
        )

    transport = httpx.MockTransport(fail_with_graphql_error)
    monkeypatch.setattr(
        "app.adapters.tatum.sui_rpc.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )

    readiness = await adapter.check_readiness()

    assert readiness["status"] == "degraded"
    assert readiness["connected"] is False
    assert readiness["error"] == "graphql_error"
    assert "unit-test-tatum-key" not in str(readiness)


@pytest.mark.asyncio
async def test_tatum_sui_balance_and_recent_activity_use_graphql(monkeypatch):
    requests = []
    client_factory = httpx.AsyncClient

    def handle_request(request):
        requests.append(request)
        query = request.content.decode()
        if "SuiBalance" in query:
            data = {
                "chainIdentifier": "4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S",
                "address": {
                    "balance": {
                        "totalBalance": "1500000000",
                    }
                }
            }
        else:
            data = {
                "chainIdentifier": "4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S",
                "address": {
                    "transactions": {
                        "nodes": [
                            {
                                "digest": "11111111111111111111111111111111",
                                "effects": {
                                    "timestamp": "2026-01-02T03:04:05Z",
                                    "status": "SUCCESS",
                                },
                            }
                        ]
                    }
                }
            }
        return httpx.Response(200, json={"data": data})

    transport = httpx.MockTransport(handle_request)
    monkeypatch.setattr(
        "app.adapters.tatum.sui_rpc.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )
    adapter = TatumSuiAdapter(api_key="unit-test-tatum-key")
    address = "0x" + "0" * 64

    balance = await adapter.get_balance(address)
    activity = await adapter.get_recent_activity(address, limit=1)

    assert balance == {
        "sui_address": address,
        "total_balance_sui": "1.5000",
        "raw_balance_mist": "1500000000",
        "network": "mainnet",
    }
    assert activity == [
        {
            "digest": "11111111111111111111111111111111",
            "timestamp_ms": 1767323045000,
            "status": "success",
        }
    ]
    assert len(requests) == 2
    assert all(request.url.path == "/graphql" for request in requests)
    assert all(request.headers["x-api-key"] == "unit-test-tatum-key" for request in requests)
    native_query = json.loads(requests[0].content)["query"]
    assert 'balance(coinType: "0x2::sui::SUI")' in native_query


@pytest.mark.asyncio
async def test_tatum_sui_invalid_graphql_response_is_not_a_success(monkeypatch):
    client_factory = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"data": {"checkpoints": {"nodes": []}}})
    )
    monkeypatch.setattr(
        "app.adapters.tatum.sui_rpc.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )
    readiness = await TatumSuiAdapter(api_key="unit-test-tatum-key").check_readiness()

    assert readiness["status"] == "degraded"
    assert readiness["connected"] is False
    assert readiness["error"] == "invalid_response"


@pytest.mark.asyncio
async def test_tatum_sui_readiness_rejects_a_mismatched_network(monkeypatch):
    client_factory = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "data": {
                    "chainIdentifier": "69WiPg3DAQiwdxfncX6wYQ2siKwAe6L9BZthQea3JNMD",
                    "checkpoints": {"nodes": [{"sequenceNumber": 12345}]},
                }
            },
        )
    )
    monkeypatch.setattr(
        "app.adapters.tatum.sui_rpc.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )

    readiness = await TatumSuiAdapter(
        api_key="unit-test-tatum-key",
        network="mainnet",
    ).check_readiness()

    assert readiness["status"] == "degraded"
    assert readiness["connected"] is False
    assert readiness["error"] == "network_mismatch"
