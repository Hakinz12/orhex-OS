"""Unit tests for decoupled provider adapters."""
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
