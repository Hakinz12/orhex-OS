"""Mocked provider and authenticated API tests for Sui wallet data."""
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.tatum.sui_rpc import TatumSuiAdapter, TatumSuiError
from app.api.v1 import sui as sui_api
from app.core.auth import provision_user
from app.core.database import Base, _enable_sqlite_foreign_keys, get_db
from app.main import app
from app.models.schemas import SuiCoinBalance, SuiWalletBalancesResponse

ADDRESS = "0x" + "a" * 64
VALID_DIGEST = "1" * 32


@pytest.fixture
async def api_harness():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield SimpleNamespace(client=client, factory=session_factory)
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


async def provision_bearer(harness):
    async with harness.factory() as session:
        _user, token = await provision_user(session)
    return {"Authorization": f"Bearer {token}"}


def install_transport(monkeypatch, handler):
    client_factory = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "app.adapters.tatum.sui_rpc.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )


@pytest.mark.asyncio
async def test_balances_query_preserves_integer_strings_and_decimal_precision(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "address": {
                        "balances": {
                            "nodes": [
                                {
                                    "coinType": {"repr": "0x2::sui::SUI"},
                                    "totalBalance": "900719925474099312345",
                                    "coinMetadata": {"decimals": 9, "symbol": "SUI"},
                                },
                                {
                                    "coinType": {"repr": "0xabc::coin::TOKEN"},
                                    "totalBalance": "123456789",
                                    "coinMetadata": None,
                                },
                            ],
                            "pageInfo": {
                                "hasNextPage": True,
                                "endCursor": "opaque/next",
                            },
                        }
                    }
                }
            },
        )

    install_transport(monkeypatch, handle)
    response = await TatumSuiAdapter(
        api_key="unit-test-tatum-key",
        rpc_url="https://tatum.example",
    ).get_wallet_balances(ADDRESS, limit=7, cursor="opaque/previous")

    assert response["balances"] == [
        {
            "coin_type": "0x2::sui::SUI",
            "raw_balance": "900719925474099312345",
            "amount": "900719925474.099312345",
            "decimals": 9,
            "symbol": "SUI",
        },
        {
            "coin_type": "0xabc::coin::TOKEN",
            "raw_balance": "123456789",
            "amount": None,
            "decimals": None,
            "symbol": None,
        },
    ]
    assert response["pagination"] == {
        "next_cursor": "opaque/next",
        "has_next_page": True,
    }
    request = requests[0]
    body = request.content.decode()
    assert "balances(first: $first, after: $after)" in body
    assert "coinMetadata" in body
    assert request.headers["x-api-key"] == "unit-test-tatum-key"
    assert request.url.path == "/graphql"
    import json

    assert json.loads(request.content)["variables"] == {
        "address": ADDRESS,
        "first": 7,
        "after": "opaque/previous",
    }


@pytest.mark.asyncio
async def test_owned_objects_query_is_bounded_and_only_returns_cursor_for_next_page(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "address": {
                        "objects": {
                            "nodes": [
                                {
                                    "address": "0x2",
                                    "version": "12",
                                    "digest": VALID_DIGEST,
                                    "contents": {
                                        "type": {"repr": "0x2::example::Item"}
                                    },
                                }
                            ],
                            "pageInfo": {
                                "hasNextPage": False,
                                "endCursor": "must-not-be-returned",
                            },
                        }
                    }
                }
            },
        )

    install_transport(monkeypatch, handle)
    response = await TatumSuiAdapter(api_key="unit-test-tatum-key").get_owned_objects(
        ADDRESS, limit=50, cursor="opaque-cursor"
    )

    assert response["objects"] == [
        {
            "object_id": "0x2",
            "version": "12",
            "digest": VALID_DIGEST,
            "type": "0x2::example::Item",
        }
    ]
    assert response["pagination"] == {
        "next_cursor": None,
        "has_next_page": False,
    }
    query = requests[0].content.decode()
    assert "objects(" in query and "filter: { owner: $address }" in query
    import json

    assert json.loads(requests[0].content)["variables"] == {
        "address": ADDRESS,
        "first": 50,
        "after": "opaque-cursor",
    }


@pytest.mark.asyncio
async def test_adapter_rejects_malformed_provider_data_and_invalid_pagination(monkeypatch):
    def malformed(_request):
        return httpx.Response(
            200,
            json={
                "data": {
                    "address": {
                        "balances": {
                            "nodes": [
                                {
                                    "coinType": {"repr": "0x2::sui::SUI"},
                                    "totalBalance": "1",
                                    "coinMetadata": {"decimals": True},
                                }
                            ],
                            "pageInfo": {
                                "hasNextPage": False,
                                "endCursor": None,
                            },
                        }
                    }
                }
            },
        )

    install_transport(monkeypatch, malformed)
    adapter = TatumSuiAdapter(api_key="unit-test-tatum-key")
    with pytest.raises(TatumSuiError) as response_error:
        await adapter.get_wallet_balances(ADDRESS)
    assert response_error.value.code == "invalid_response"

    with pytest.raises(ValueError):
        await adapter.get_wallet_balances(ADDRESS, limit=51)
    with pytest.raises(ValueError):
        await adapter.get_owned_objects(ADDRESS, cursor=" padded ")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "expected_code"),
    [
        (
            lambda _request: httpx.Response(502, json={"message": "secret upstream body"}),
            "http_502",
        ),
        (
            lambda _request: httpx.Response(
                200,
                json={
                    "errors": [{"message": "secret upstream body"}],
                    "data": None,
                },
            ),
            "graphql_error",
        ),
        (
            lambda request: (_ for _ in ()).throw(
                httpx.ReadTimeout("secret timeout", request=request)
            ),
            "timeout",
        ),
    ],
)
async def test_provider_errors_are_normalized_without_upstream_body(
    monkeypatch, handler, expected_code
):
    install_transport(monkeypatch, handler)
    with pytest.raises(TatumSuiError) as response_error:
        await TatumSuiAdapter(api_key="unit-test-tatum-key").get_owned_objects(ADDRESS)
    assert response_error.value.code == expected_code
    assert "secret upstream body" not in str(response_error.value)
    assert "unit-test-tatum-key" not in str(response_error.value)


@pytest.mark.asyncio
async def test_sui_api_requires_authentication_and_validates_address_and_pagination(
    api_harness, monkeypatch
):
    monkeypatch.setattr(sui_api._sui_adapter, "api_key", "unit-test-tatum-key")
    unauthorized = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/balances"
    )
    assert unauthorized.status_code == 401

    headers = await provision_bearer(api_harness)
    invalid_address = await api_harness.client.get(
        "/api/v1/sui/wallet/not-an-address/objects",
        headers=headers,
    )
    invalid_limit = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/objects?limit=51",
        headers=headers,
    )
    invalid_cursor = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/objects?cursor=%20padded%20",
        headers=headers,
    )
    assert invalid_address.status_code == 422
    assert invalid_limit.status_code == 422
    assert invalid_cursor.status_code == 422


@pytest.mark.asyncio
async def test_sui_api_returns_validated_balances_and_safe_provider_errors(
    api_harness, monkeypatch
):
    headers = await provision_bearer(api_harness)
    monkeypatch.setattr(sui_api._sui_adapter, "api_key", "unit-test-tatum-key")
    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_wallet_balances",
        lambda *_args, **_kwargs: _balances_result(),
    )

    response = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/balances?limit=3&cursor=next",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["balances"][0]["raw_balance"] == "1500000000"
    assert response.json()["pagination"]["has_next_page"] is False

    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_owned_objects",
        lambda *_args, **_kwargs: _provider_error(),
    )
    failure = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/objects",
        headers=headers,
    )
    assert failure.status_code == 502
    assert failure.json()["error"] == "provider_error"
    assert "credential-never-return" not in failure.text

    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_owned_objects",
        lambda *_args, **_kwargs: _provider_timeout(),
    )
    timeout = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/objects",
        headers=headers,
    )
    assert timeout.status_code == 503
    assert timeout.json()["error"] == "provider_unavailable"
    assert "secret-timeout-detail" not in timeout.text


@pytest.mark.asyncio
async def test_sui_api_reports_missing_credentials_without_calling_provider(
    api_harness, monkeypatch
):
    headers = await provision_bearer(api_harness)
    monkeypatch.setattr(sui_api._sui_adapter, "api_key", "")
    response = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/balances",
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["error"] == "provider_unconfigured"


async def _balances_result():
    return SuiWalletBalancesResponse(
        sui_address=ADDRESS,
        network="mainnet",
        balances=[
            SuiCoinBalance(
                coin_type="0x2::sui::SUI",
                raw_balance="1500000000",
                amount="1.5",
                decimals=9,
                symbol="SUI",
            )
        ],
    ).model_dump()


async def _provider_error():
    raise TatumSuiError("graphql_error", "credential-never-return")


async def _provider_timeout():
    raise TatumSuiError("timeout", "secret-timeout-detail")
