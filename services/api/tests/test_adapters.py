"""Unit tests for decoupled provider adapters."""
import httpx
import pytest
from app.adapters.llm.gemini import GeminiAdapter
from app.adapters.walrus.memwal import MemWalAdapter
from app.adapters.tatum.sui_rpc import TatumSuiAdapter
from app.core.config import settings


@pytest.mark.asyncio
async def test_gemini_adapter_unconfigured_behavior(monkeypatch):
    """Verify Gemini adapter handles missing credentials safely without throwing exceptions."""
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "environment-key-is-not-used")
    adapter = GeminiAdapter(api_key="")
    readiness = await adapter.check_readiness()
    assert readiness["status"] == "unconfigured"
    assert "GEMINI_API_KEY" in readiness["message"]

    res = await adapter.generate_response(
        system_prompt="Test",
        user_message="Hello",
        recalled_memories=[]
    )
    assert "GEMINI_API_KEY" in res["answer"]


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
                "address": {
                    "balance": {
                        "totalBalance": "1500000000",
                    }
                }
            }
        else:
            data = {
                "address": {
                    "transactions": {
                        "nodes": [
                            {
                                "digest": "public-transaction",
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
            "digest": "public-transaction",
            "timestamp_ms": 1767323045000,
            "status": "success",
        }
    ]
    assert len(requests) == 2
    assert all(request.url.path == "/graphql" for request in requests)
    assert all(request.headers["x-api-key"] == "unit-test-tatum-key" for request in requests)


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
