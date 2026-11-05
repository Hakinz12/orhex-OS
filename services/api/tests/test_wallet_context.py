"""Focused tests for verified, request-time visitor wallet context retrieval."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.tatum.sui_rpc import TatumSuiError
from app.core.config import settings
from app.core.database import Base, _enable_sqlite_foreign_keys
from app.models.domain import WalletAssociation
from app.services.anonymous_visitor_identity import provision_visitor_identity
from app.services.wallet_context import SUI_COIN_TYPE, retrieve_visitor_wallet_context

ADDRESS = "0x" + "a" * 64


@pytest.fixture
async def context_harness():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield SimpleNamespace(factory=factory)
    finally:
        await engine.dispose()


async def _provision_visitor(harness):
    async with harness.factory() as session:
        return await provision_visitor_identity(session)


async def _link_wallet(harness, visitor, *, address=ADDRESS, network="mainnet", chain="sui"):
    association = WalletAssociation(
        visitor_identity_id=visitor.identity.id,
        user_id=visitor.user.id,
        chain=chain,
        network=network,
        canonical_address=address,
        verified_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    async with harness.factory() as session:
        session.add(association)
        await session.commit()
    return association


class MockTatum:
    api_key = "test-key"
    network = "mainnet"

    def __init__(self, *, native=None, balances=None, error=None):
        self.native = native or {
            "sui_address": ADDRESS,
            "total_balance_sui": "1.25",
            "raw_balance_mist": "1250000001",
            "network": "mainnet",
        }
        self.balances = balances or {
            "sui_address": ADDRESS,
            "network": "mainnet",
            "balances": [
                {
                    "coin_type": SUI_COIN_TYPE,
                    "raw_balance": "1250000000",
                    "amount": "1.25",
                    "decimals": 9,
                    "symbol": "SUI",
                },
                {
                    "coin_type": "0xabc::token::TOKEN",
                    "raw_balance": "2500",
                    "amount": "25",
                    "decimals": 2,
                    "symbol": "TOK",
                },
            ],
            "pagination": {"next_cursor": None, "has_next_page": False},
        }
        self.error = error
        self.addresses = []

    async def get_balance(self, address):
        self.addresses.append(address)
        if self.error:
            raise self.error
        return self.native

    async def get_wallet_balances(self, address, *, limit):
        self.addresses.append(address)
        assert limit > 0
        if self.error:
            raise self.error
        return self.balances


@pytest.mark.asyncio
async def test_no_verified_association_returns_no_wallet(context_harness):
    visitor = await _provision_visitor(context_harness)
    adapter = MockTatum()

    async with context_harness.factory() as session:
        result = await retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
            adapter=adapter,
        )

    assert result.status == "no_wallet"
    assert result.address is None
    assert result.network is None
    assert result.error_code is None
    assert adapter.addresses == []


@pytest.mark.asyncio
async def test_verified_wallet_returns_native_and_token_balances(context_harness, monkeypatch):
    monkeypatch.setattr(settings, "SUI_NETWORK", "mainnet")
    visitor = await _provision_visitor(context_harness)
    await _link_wallet(context_harness, visitor)
    adapter = MockTatum()

    async with context_harness.factory() as session:
        result = await retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
            adapter=adapter,
        )

    assert result.status == "retrieved"
    assert result.address == ADDRESS
    assert result.network == "mainnet"
    assert result.native_balance is not None
    assert result.native_balance.raw_balance == "1250000001"
    assert result.native_balance.amount == "1.250000001"
    assert [balance.coin_type for balance in result.token_balances] == [
        "0xabc::token::TOKEN"
    ]
    assert result.error_code is None
    assert adapter.addresses == [ADDRESS, ADDRESS]


@pytest.mark.asyncio
async def test_revoked_wallet_association_returns_no_wallet(context_harness):
    visitor = await _provision_visitor(context_harness)
    association = await _link_wallet(context_harness, visitor)
    async with context_harness.factory() as session:
        row = await session.get(WalletAssociation, association.id)
        row.revoked_at = datetime.now(timezone.utc)
        await session.commit()

    adapter = MockTatum()
    async with context_harness.factory() as session:
        result = await retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
            adapter=adapter,
        )

    assert result.status == "no_wallet"
    assert result.address is None
    assert adapter.addresses == []


@pytest.mark.asyncio
async def test_wallet_network_mismatch_is_rejected_without_exposing_identity(
    context_harness,
    monkeypatch,
):
    monkeypatch.setattr(settings, "SUI_NETWORK", "mainnet")
    visitor = await _provision_visitor(context_harness)
    await _link_wallet(context_harness, visitor, network="testnet")
    adapter = MockTatum()

    async with context_harness.factory() as session:
        result = await retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
            adapter=adapter,
        )

    assert result.status == "unavailable"
    assert result.address is None
    assert result.network is None
    assert result.error_code == "association_invalid"
    assert adapter.addresses == []


@pytest.mark.asyncio
async def test_tatum_timeout_returns_safe_unavailable_context(context_harness):
    visitor = await _provision_visitor(context_harness)
    await _link_wallet(context_harness, visitor)
    adapter = MockTatum(
        error=TatumSuiError("timeout", "private provider failure detail")
    )

    async with context_harness.factory() as session:
        result = await retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
            adapter=adapter,
        )

    assert result.status == "unavailable"
    assert result.address == ADDRESS
    assert result.network == "mainnet"
    assert result.error_code == "provider_unavailable"
    assert "private" not in result.model_dump_json()
    assert "timeout" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_raw_timeout_returns_safe_unavailable_context(context_harness):
    visitor = await _provision_visitor(context_harness)
    await _link_wallet(context_harness, visitor)
    adapter = MockTatum(error=TimeoutError("private timeout details"))

    async with context_harness.factory() as session:
        result = await retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
            adapter=adapter,
        )

    assert result.status == "unavailable"
    assert result.address == ADDRESS
    assert result.error_code == "provider_unavailable"
    assert "private" not in result.model_dump_json()
    assert "timeout" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_tatum_configuration_failure_returns_safe_unavailable_context(
    context_harness,
    monkeypatch,
):
    visitor = await _provision_visitor(context_harness)
    await _link_wallet(context_harness, visitor)
    adapter = MockTatum()
    adapter.api_key = ""

    async with context_harness.factory() as session:
        result = await retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
            adapter=adapter,
        )

    assert result.status == "unavailable"
    assert result.address == ADDRESS
    assert result.error_code == "provider_unavailable"
    assert adapter.addresses == []


@pytest.mark.asyncio
async def test_tatum_adapter_initialization_failure_is_safely_reported(
    context_harness,
    monkeypatch,
):
    import app.services.wallet_context as wallet_context

    visitor = await _provision_visitor(context_harness)
    await _link_wallet(context_harness, visitor)

    def fail_initialization():
        raise ValueError("private configuration detail")

    monkeypatch.setattr(wallet_context, "TatumSuiAdapter", fail_initialization)
    async with context_harness.factory() as session:
        result = await wallet_context.retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
        )

    assert result.status == "unavailable"
    assert result.address == ADDRESS
    assert result.error_code == "provider_unavailable"
    assert "private" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_invalid_tatum_url_is_safely_reported(context_harness):
    visitor = await _provision_visitor(context_harness)
    await _link_wallet(context_harness, visitor)
    adapter = MockTatum(error=__import__("httpx").InvalidURL("private URL details"))

    async with context_harness.factory() as session:
        result = await retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
            adapter=adapter,
        )

    assert result.status == "unavailable"
    assert result.address == ADDRESS
    assert result.error_code == "provider_unavailable"
    assert "private" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_adapter_network_mismatch_is_unavailable_without_provider_call(
    context_harness,
):
    visitor = await _provision_visitor(context_harness)
    await _link_wallet(context_harness, visitor)
    adapter = MockTatum()
    adapter.network = "testnet"

    async with context_harness.factory() as session:
        result = await retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
            adapter=adapter,
        )

    assert result.status == "unavailable"
    assert result.address == ADDRESS
    assert result.network == "mainnet"
    assert result.error_code == "provider_unavailable"
    assert adapter.addresses == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("native", "balances"),
    [
        ({"raw_balance_mist": "bad"}, None),
        (
            None,
            {
                "sui_address": ADDRESS,
                "network": "mainnet",
                "balances": [{"coin_type": "0xabc::token::TOKEN"}],
                "pagination": {"next_cursor": None, "has_next_page": False},
            },
        ),
        (
            None,
            {
                "sui_address": ADDRESS,
                "network": "mainnet",
                "balances": [
                    {
                        "coin_type": " ",
                        "raw_balance": "1",
                        "amount": "1",
                        "decimals": 0,
                        "symbol": "BAD",
                    }
                ],
                "pagination": {"next_cursor": None, "has_next_page": False},
            },
        ),
    ],
)
async def test_malformed_provider_balances_return_safe_unavailable(
    context_harness,
    native,
    balances,
):
    visitor = await _provision_visitor(context_harness)
    await _link_wallet(context_harness, visitor)
    kwargs = {}
    if native is not None:
        kwargs["native"] = native
    if balances is not None:
        kwargs["balances"] = balances
    adapter = MockTatum(**kwargs)

    async with context_harness.factory() as session:
        result = await retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
            adapter=adapter,
        )

    assert result.status == "unavailable"
    assert result.address == ADDRESS
    assert result.error_code == "provider_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("canonical_address", "not-an-address"),
        ("verified_at", None),
        ("verified_at", "invalid-timestamp"),
    ],
)
async def test_invalid_stored_association_is_unavailable_without_leaking_identity(
    context_harness,
    field,
    value,
):
    visitor = await _provision_visitor(context_harness)
    association = await _link_wallet(context_harness, visitor)
    async with context_harness.factory() as session:
        row = await session.get(type(association), association.id)
        setattr(row, field, value)
        with session.no_autoflush:
            adapter = MockTatum()
            result = await retrieve_visitor_wallet_context(
                session,
                user_id=visitor.user.id,
                visitor_identity_id=visitor.identity.id,
                adapter=adapter,
            )

    assert result.status == "unavailable"
    assert result.address is None
    assert result.network is None
    assert result.error_code == "association_invalid"
    assert adapter.addresses == []


@pytest.mark.asyncio
async def test_unverified_address_is_not_an_input_to_context_service(context_harness):
    import inspect

    visitor = await _provision_visitor(context_harness)
    adapter = MockTatum()
    parameters = inspect.signature(retrieve_visitor_wallet_context).parameters
    assert "address" not in parameters

    async with context_harness.factory() as session:
        result = await retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
            adapter=adapter,
        )

    assert result.status == "no_wallet"
    assert result.address is None
    assert adapter.addresses == []


@pytest.mark.asyncio
async def test_wallet_context_cannot_cross_user_or_visitor_boundaries(context_harness):
    visitor = await _provision_visitor(context_harness)
    another_visitor = await _provision_visitor(context_harness)
    await _link_wallet(context_harness, visitor)

    for user_id, visitor_id in (
        (another_visitor.user.id, another_visitor.identity.id),
        (visitor.user.id, another_visitor.identity.id),
        (another_visitor.user.id, visitor.identity.id),
    ):
        adapter = MockTatum()
        async with context_harness.factory() as session:
            result = await retrieve_visitor_wallet_context(
                session,
                user_id=user_id,
                visitor_identity_id=visitor_id,
                adapter=adapter,
            )
        assert result.status == "no_wallet"
        assert result.address is None
        assert adapter.addresses == []


@pytest.mark.asyncio
async def test_provider_returning_wrong_address_is_safe_unavailable(context_harness):
    visitor = await _provision_visitor(context_harness)
    await _link_wallet(context_harness, visitor)
    wrong_address_payload = {
        "sui_address": "0x" + "b" * 64,
        "total_balance_sui": "1",
        "raw_balance_mist": "1000000000",
        "network": "mainnet",
    }
    adapter = MockTatum(native=wrong_address_payload)

    async with context_harness.factory() as session:
        result = await retrieve_visitor_wallet_context(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
            adapter=adapter,
        )

    assert result.status == "unavailable"
    assert result.address == ADDRESS
    assert result.network == "mainnet"
    assert result.error_code == "provider_unavailable"
