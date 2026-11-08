"""Mocked provider and authenticated API tests for Sui wallet data."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.tatum.sui_rpc import TatumSuiAdapter, TatumSuiError
from app.api import dependencies
from app.api.v1 import sui as sui_api
from app.core.auth import provision_user
from app.core.database import Base, _enable_sqlite_foreign_keys, get_db
from app.core.rate_limit import SensitiveEndpointRateLimiter
from app.main import app
from app.models.domain import AnonymousVisitorCredential, AnonymousVisitorIdentity, User
from app.models.schemas import (
    SuiCoinBalance,
    SuiOwnedObjectsResponse,
    SuiWalletBalancesResponse,
)
from app.services import anonymous_visitor_identity as visitor_service
from app.services.anonymous_visitor_identity import (
    provision_visitor_identity,
    revoke_visitor_credential,
)

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


async def provision_visitor_credential(harness):
    async with harness.factory() as session:
        issued = await provision_visitor_identity(session)
    return {"Authorization": f"Bearer {issued.credential}"}


def install_transport(monkeypatch, handler):
    client_factory = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "app.adapters.tatum.sui_rpc.httpx.AsyncClient",
        lambda **kwargs: client_factory(transport=transport, **kwargs),
    )


def client_for_peer(host):
    return AsyncClient(
        transport=ASGITransport(app=app, client=(host, 54321)),
        base_url="http://test",
    )


async def provision_visitor_issue(harness):
    async with harness.factory() as session:
        return await provision_visitor_identity(session)


async def _objects_result():
    return SuiOwnedObjectsResponse(
        sui_address=ADDRESS,
        network="mainnet",
        objects=[],
        pagination={"next_cursor": None, "has_next_page": False},
    ).model_dump()


def install_api_provider(monkeypatch):
    monkeypatch.setattr(sui_api._sui_adapter, "api_key", "test-provider-key")
    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_wallet_balances",
        lambda *_args, **_kwargs: _balances_result(),
    )
    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_owned_objects",
        lambda *_args, **_kwargs: _objects_result(),
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
                    "chainIdentifier": "4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S",
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
                    "chainIdentifier": "4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S",
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
                    "chainIdentifier": "4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S",
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
async def test_adapter_accepts_empty_balance_and_object_pages(monkeypatch):
    def handle(request):
        query = request.content.decode()
        connection = "balances" if "SuiAddressBalances" in query else "objects"
        return httpx.Response(200, json={"data": {
            "chainIdentifier": "4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S",
            "address": {
                connection: {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            },
        }})

    install_transport(monkeypatch, handle)
    adapter = TatumSuiAdapter(api_key="unit-test-tatum-key")
    balances = await adapter.get_wallet_balances(ADDRESS)
    objects = await adapter.get_owned_objects(ADDRESS)
    assert balances["balances"] == []
    assert balances["pagination"] == {"next_cursor": None, "has_next_page": False}
    assert objects["objects"] == []
    assert objects["pagination"] == {"next_cursor": None, "has_next_page": False}


@pytest.mark.asyncio
async def test_adapter_rejects_unverified_devnet_and_wrong_chain_for_balances(monkeypatch):
    provider = {"chain": "69WiPg3DAQiwdxfncX6wYQ2siKwAe6L9BZthQea3JNMD"}

    def handle(_request):
        return httpx.Response(200, json={"data": {
            "chainIdentifier": provider["chain"],
            "address": {},
        }})

    install_transport(monkeypatch, handle)
    with pytest.raises(TatumSuiError, match="different network") as mismatch:
        await TatumSuiAdapter(
            api_key="unit-test-tatum-key", network="mainnet",
        ).get_wallet_balances(ADDRESS)
    assert mismatch.value.code == "network_mismatch"

    provider["chain"] = "4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S"
    with pytest.raises(TatumSuiError, match="not verified") as unsupported:
        await TatumSuiAdapter(
            api_key="unit-test-tatum-key", network="devnet",
        ).get_wallet_balances(ADDRESS)
    assert unsupported.value.code == "unsupported_network"


@pytest.mark.asyncio
async def test_adapter_rejects_malformed_transaction_digest(monkeypatch):
    install_transport(monkeypatch, lambda _request: httpx.Response(
        200,
        json={"data": {
            "chainIdentifier": "4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S",
            "address": {"transactions": {"nodes": [
                {"digest": "not-a-sui-digest", "effects": {"status": "success"}},
            ]}},
        }},
    ))
    with pytest.raises(TatumSuiError) as malformed:
        await TatumSuiAdapter(api_key="unit-test-tatum-key").get_recent_activity(
            ADDRESS,
        )
    assert malformed.value.code == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "expected_code"),
    [
        (
            lambda _request: httpx.Response(502, json={"message": "secret upstream body"}),
            "http_502",
        ),
        (
            lambda _request: httpx.Response(401, json={"message": "secret authentication detail"}),
            "authentication_error",
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
async def test_sui_api_returns_native_balance_and_normalizes_balance_provider_failures(
    api_harness, monkeypatch
):
    headers = await provision_bearer(api_harness)
    monkeypatch.setattr(sui_api._sui_adapter, "api_key", "test-provider-key")
    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_wallet_balances",
        lambda *_args, **_kwargs: _balances_result(),
    )
    success = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/balances",
        headers=headers,
    )
    assert success.status_code == 200
    assert success.json()["balances"][0]["amount"] == "1.5"
    assert success.json()["balances"][0]["raw_balance"] == "1500000000"

    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_wallet_balances",
        lambda *_args, **_kwargs: _provider_error(),
    )
    failure = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/balances",
        headers=headers,
    )
    assert failure.status_code == 502
    assert failure.json()["error"] == "provider_error"
    assert "credential-never-return" not in failure.text

    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_wallet_balances",
        lambda *_args, **_kwargs: _provider_timeout(),
    )
    timeout = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/balances",
        headers=headers,
    )
    assert timeout.status_code == 503
    assert timeout.json()["error"] == "provider_unavailable"


@pytest.mark.asyncio
async def test_sui_api_accepts_empty_balance_and_object_results(api_harness, monkeypatch):
    headers = await provision_bearer(api_harness)
    install_api_provider(monkeypatch)

    async def empty_balances(*_args, **_kwargs):
        return SuiWalletBalancesResponse(
            sui_address=ADDRESS,
            network="mainnet",
            balances=[],
        ).model_dump()

    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_wallet_balances",
        empty_balances,
    )
    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_owned_objects",
        lambda *_args, **_kwargs: _objects_result(),
    )

    balances = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/balances",
        headers=headers,
    )
    objects = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/objects",
        headers=headers,
    )
    assert balances.status_code == 200
    assert balances.json()["balances"] == []
    assert objects.status_code == 200
    assert objects.json()["objects"] == []


@pytest.mark.asyncio
async def test_sui_api_accepts_visitor_credentials_and_rejects_malformed_visitor_tokens(
    api_harness, monkeypatch
):
    visitor_headers = await provision_visitor_credential(api_harness)
    monkeypatch.setattr(sui_api._sui_adapter, "api_key", "unit-test-tatum-key")
    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_wallet_balances",
        lambda *_args, **_kwargs: _balances_result(),
    )

    response = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/balances",
        headers=visitor_headers,
    )
    assert response.status_code == 200

    bad = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/balances",
        headers={"Authorization": "Bearer not-a-visitor-token"},
    )
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_visitor_sui_reads_preserve_owner_and_ignore_client_ownership_claims(
    api_harness, monkeypatch
):
    issue = await provision_visitor_issue(api_harness)
    install_api_provider(monkeypatch)

    balances = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/balances"
        "?user_id=attacker&visitor_id=attacker&namespace=attacker",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )
    objects = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/objects"
        "?user_id=attacker&visitor_id=attacker&namespace=attacker",
        headers={"Authorization": f"Bearer {issue.credential}"},
    )

    assert balances.status_code == 200
    assert objects.status_code == 200
    assert set(balances.json()) == {"sui_address", "network", "balances", "pagination"}
    assert set(objects.json()) == {"sui_address", "network", "objects", "pagination"}
    assert "attacker" not in balances.text + objects.text

    async with api_harness.factory() as session:
        identity_count = await session.scalar(
            select(func.count(AnonymousVisitorIdentity.id))
        )
        user_count = await session.scalar(select(func.count(User.id)))
        identity = await session.get(AnonymousVisitorIdentity, issue.identity.id)

    assert identity_count == 1
    assert user_count == 1
    assert identity.user_id == issue.user.id


@pytest.mark.asyncio
async def test_pat_can_read_both_sui_resources(api_harness, monkeypatch):
    headers = await provision_bearer(api_harness)
    install_api_provider(monkeypatch)

    balances = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/balances",
        headers=headers,
    )
    objects = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/objects",
        headers=headers,
    )

    assert balances.status_code == 200
    assert objects.status_code == 200


@pytest.mark.asyncio
async def test_pat_and_visitor_credentials_do_not_cross_authenticate(api_harness):
    visitor = await provision_visitor_issue(api_harness)
    async with api_harness.factory() as session:
        _user, pat = await provision_user(session)

    async with api_harness.factory() as session:
        with pytest.raises(HTTPException) as visitor_as_pat:
            await dependencies.get_current_user(
                credentials=dependencies.HTTPAuthorizationCredentials(
                    scheme="Bearer",
                    credentials=visitor.credential,
                ),
                db=session,
            )
        with pytest.raises(HTTPException) as pat_as_visitor:
            await dependencies.get_current_visitor(
                credentials=dependencies.HTTPAuthorizationCredentials(
                    scheme="Bearer",
                    credentials=pat,
                ),
                db=session,
            )

    assert visitor_as_pat.value.status_code == 401
    assert pat_as_visitor.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("credential_state", ["malformed", "unknown", "expired", "revoked"])
async def test_sui_rejects_invalid_expired_and_revoked_visitor_credentials(
    api_harness, monkeypatch, credential_state
):
    issue = await provision_visitor_issue(api_harness)
    credential = issue.credential
    if credential_state == "malformed":
        credential = "not-a-visitor-credential"
    elif credential_state == "unknown":
        credential = "orv_" + ("z" * 43)
    elif credential_state == "expired":
        fixed_now = datetime(2030, 4, 5, 6, 7, 8, tzinfo=timezone.utc)
        monkeypatch.setattr(visitor_service, "_utc_now", lambda: fixed_now)
        async with api_harness.factory() as session:
            record = (
                await session.execute(
                    select(AnonymousVisitorCredential).where(
                        AnonymousVisitorCredential.visitor_id == issue.identity.id
                    )
                )
            ).scalar_one()
            record.expires_at = fixed_now - timedelta(seconds=1)
            await session.commit()
    elif credential_state == "revoked":
        async with api_harness.factory() as session:
            assert await revoke_visitor_credential(session, credential)

    install_api_provider(monkeypatch)
    response = await api_harness.client.get(
        f"/api/v1/sui/wallet/{ADDRESS}/balances",
        headers={"Authorization": f"Bearer {credential}"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["balances", "objects"])
async def test_sui_rate_limit_uses_direct_peer_and_returns_safe_retry_after(
    api_harness, monkeypatch, resource
):
    issue_headers = await provision_bearer(api_harness)
    install_api_provider(monkeypatch)
    monkeypatch.setattr(
        sui_api,
        "_sui_rate_limiter",
        SensitiveEndpointRateLimiter(requests_per_minute=2, window_seconds=60),
    )
    path = f"/api/v1/sui/wallet/{ADDRESS}/{resource}"

    async with client_for_peer("192.0.2.10") as client:
        first = await client.get(
            path,
            headers={**issue_headers, "X-Forwarded-For": "198.51.100.1"},
        )
        second = await client.get(
            path,
            headers={**issue_headers, "Forwarded": "for=198.51.100.2"},
        )
        limited = await client.get(
            path,
            headers={**issue_headers, "X-Forwarded-For": "203.0.113.99"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert limited.status_code == 429
    assert limited.json()["error"] == "rate_limited"
    assert 1 <= int(limited.headers["Retry-After"]) <= 60
    assert "198.51.100" not in limited.text
    assert "203.0.113" not in limited.text

    async with client_for_peer("192.0.2.11") as other_client:
        isolated = await other_client.get(path, headers=issue_headers)
    assert isolated.status_code == 200


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


@pytest.mark.asyncio
async def test_wallet_intelligence_marks_malformed_activity_partial_and_keeps_other_reads(
    api_harness, monkeypatch
):
    headers = await provision_bearer(api_harness)
    install_api_provider(monkeypatch)

    async def activity(*_args, **_kwargs):
        return [{"digest": "not-a-sui-digest", "status": "success"}]

    async def checkpoint():
        return 123

    monkeypatch.setattr(sui_api._sui_adapter, "get_recent_activity", activity)
    monkeypatch.setattr(sui_api._sui_adapter, "get_latest_checkpoint", checkpoint)
    invalid = await api_harness.client.get(
        f"/api/v1/sui/intelligence/{ADDRESS}", headers=headers,
    )
    assert invalid.status_code == 200
    assert invalid.json()["data_status"] == "partial"
    assert invalid.json()["unavailable_sections"] == ["activity"]
    assert invalid.json()["activity"] is None

    async def valid_activity(*_args, **_kwargs):
        return [{
            "digest": VALID_DIGEST,
            "timestamp_ms": 1,
            "status": "success",
        }]

    monkeypatch.setattr(sui_api._sui_adapter, "get_recent_activity", valid_activity)
    valid = await api_harness.client.get(
        f"/api/v1/sui/intelligence/{ADDRESS}", headers=headers,
    )
    assert valid.status_code == 200
    assert valid.json()["data_status"] == "complete"
    assert valid.json()["checkpoint"] == 123
    assert valid.json()["snapshot_consistency"] == "independent_provider_reads"


@pytest.mark.asyncio
async def test_wallet_intelligence_normalizes_provider_failure(api_harness, monkeypatch):
    headers = await provision_bearer(api_harness)
    install_api_provider(monkeypatch)
    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_recent_activity",
        lambda *_args, **_kwargs: _provider_timeout(),
    )
    monkeypatch.setattr(
        sui_api._sui_adapter,
        "get_latest_checkpoint",
        lambda: _checkpoint_result(123),
    )
    response = await api_harness.client.get(
        f"/api/v1/sui/intelligence/{ADDRESS}", headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["data_status"] == "partial"
    assert payload["unavailable_sections"] == ["activity"]
    assert payload["portfolio"]["known_token_count"] == 1
    assert payload["activity"] is None
    assert payload["checkpoint"] == 123
    assert "secret-timeout-detail" not in response.text


@pytest.mark.asyncio
async def test_wallet_intelligence_preserves_successful_reads_on_provider_auth_failure(
    api_harness, monkeypatch
):
    headers = await provision_bearer(api_harness)
    install_api_provider(monkeypatch)

    async def authentication_failure(*_args, **_kwargs):
        raise TatumSuiError("authentication_error", "do-not-return-provider-secret")

    async def valid_activity(*_args, **_kwargs):
        return []

    async def valid_checkpoint():
        return 456

    monkeypatch.setattr(sui_api._sui_adapter, "get_wallet_balances", authentication_failure)
    monkeypatch.setattr(sui_api._sui_adapter, "get_recent_activity", valid_activity)
    monkeypatch.setattr(sui_api._sui_adapter, "get_latest_checkpoint", valid_checkpoint)
    response = await api_harness.client.get(
        f"/api/v1/sui/intelligence/{ADDRESS}", headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["data_status"] == "partial"
    assert payload["unavailable_sections"] == ["balances"]
    assert payload["provider_errors"] == [
        {"section": "balances", "code": "authentication_failed"}
    ]
    assert payload["portfolio"] is None
    assert payload["activity"]["sample_size"] == 0
    assert payload["checkpoint"] == 456
    assert "do-not-return-provider-secret" not in response.text


async def _checkpoint_result(value):
    return value
