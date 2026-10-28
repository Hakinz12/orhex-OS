"""Unit tests for decoupled provider adapters."""
import pytest
from app.adapters.llm.gemini import GeminiAdapter
from app.adapters.walrus.memwal import MemWalAdapter
from app.adapters.tatum.sui_rpc import TatumSuiAdapter


@pytest.mark.asyncio
async def test_gemini_adapter_unconfigured_behavior():
    """Verify Gemini adapter handles missing credentials safely without throwing exceptions."""
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
    adapter = MemWalAdapter(agent_id="", private_key="")
    readiness = await adapter.check_readiness()
    assert readiness["configured"] is False
    assert readiness["agent_id"] == "not_set"

    # Memory persistence must fail safely if agent_id is missing
    result = await adapter.remember(namespace="test_ns", text="Test memory")
    assert result["status"] == "failed"
    assert "MEMWAL_AGENT_ID" in result["error"]


@pytest.mark.asyncio
async def test_tatum_sui_adapter_unconfigured_behavior():
    """Verify Tatum adapter initializes safely and formats query structure."""
    adapter = TatumSuiAdapter(api_key="", rpc_url="https://sui-mainnet.gateway.tatum.io")
    assert adapter.network == "mainnet"

    # Balance query with invalid address / unconfigured state should gracefully report fallback
    balance_res = await adapter.get_balance("0x0000000000000000000000000000000000000000000000000000000000000000")
    assert balance_res["sui_address"] == "0x0000000000000000000000000000000000000000000000000000000000000000"
    assert "total_balance_sui" in balance_res

