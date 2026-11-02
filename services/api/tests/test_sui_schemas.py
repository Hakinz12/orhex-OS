"""Validation and serialization tests for the proposed Sui API contracts."""

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    SuiCoinBalance,
    SuiErrorResponse,
    SuiHealthResponse,
    SuiNetworkResponse,
    SuiOwnedObject,
    SuiOwnedObjectsResponse,
    SuiPaginationMetadata,
    SuiPaginationQuery,
    SuiTransactionResponse,
    SuiWalletBalancesResponse,
    SuiWalletRegisterRequest,
    validate_sui_address,
    validate_sui_transaction_digest,
)


VALID_ADDRESS = "0x" + "a" * 64
VALID_DIGEST = "1" * 32


@pytest.mark.parametrize(
    "address",
    [VALID_ADDRESS, "0x2", "0x" + "B" * 64],
)
def test_sui_address_accepts_full_and_short_hex_forms(address):
    assert validate_sui_address(address) == address
    assert SuiWalletRegisterRequest(sui_address=address).sui_address == address


@pytest.mark.parametrize(
    "address",
    ["", "2", "0x", "0x" + "a" * 65, "0x12xz", "0X" + "a" * 64],
)
def test_sui_address_rejects_malformed_values(address):
    with pytest.raises(ValueError):
        validate_sui_address(address)


def test_sui_transaction_digest_requires_base58_encoded_32_bytes():
    assert validate_sui_transaction_digest(VALID_DIGEST) == VALID_DIGEST
    assert SuiTransactionResponse(digest=VALID_DIGEST).digest == VALID_DIGEST


@pytest.mark.parametrize(
    "digest",
    ["", "1" * 31, "1" * 33, "0" * 32, "O" * 32, "public-transaction"],
)
def test_sui_transaction_digest_rejects_invalid_values(digest):
    with pytest.raises(ValueError):
        validate_sui_transaction_digest(digest)


def test_pagination_query_accepts_optional_opaque_cursor_and_default_limit():
    query = SuiPaginationQuery()
    assert query.cursor is None
    assert query.limit == 20

    query_with_cursor = SuiPaginationQuery(cursor="opaque/cursor-1", limit=50)
    assert query_with_cursor.cursor == "opaque/cursor-1"
    assert query_with_cursor.limit == 50
    assert SuiPaginationQuery(limit="10").limit == 10

    assert SuiPaginationMetadata(
        next_cursor="opaque/cursor-2",
        has_next_page=True,
    ).model_dump() == {
        "next_cursor": "opaque/cursor-2",
        "has_next_page": True,
    }


@pytest.mark.parametrize("limit", [0, -1, 51, True, "many"])
def test_pagination_query_rejects_invalid_limits(limit):
    with pytest.raises(ValidationError):
        SuiPaginationQuery(limit=limit)


@pytest.mark.parametrize("cursor", ["", " padded ", "line\nbreak", "x" * 513])
def test_pagination_query_rejects_invalid_cursors(cursor):
    with pytest.raises(ValidationError):
        SuiPaginationQuery(cursor=cursor)


def test_sui_response_models_serialize_normalized_data():
    network = SuiNetworkResponse(
        network="mainnet",
        endpoint="https://sui-mainnet.gateway.tatum.io",
        chain_identifier="4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S",
    )
    health = SuiHealthResponse(
        status="ready",
        configured=True,
        connected=True,
        network="mainnet",
        endpoint="https://sui-mainnet.gateway.tatum.io",
        latest_checkpoint=12345,
    )
    balances = SuiWalletBalancesResponse(
        sui_address="0x2",
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
    )
    objects = SuiOwnedObjectsResponse(
        sui_address=VALID_ADDRESS,
        network="mainnet",
        objects=[
            SuiOwnedObject(
                object_id="0x3",
                version="1",
                digest=VALID_DIGEST,
                type="0x2::example::Item",
            )
        ],
        pagination=SuiPaginationMetadata(
            next_cursor=None,
            has_next_page=False,
        ),
    )
    transaction = SuiTransactionResponse(
        digest=VALID_DIGEST,
        timestamp_ms=1767323045000,
        status="success",
    )

    assert network.model_dump()["chain_identifier"].startswith("4bti")
    assert health.model_dump()["latest_checkpoint"] == 12345
    assert balances.model_dump()["balances"][0]["raw_balance"] == "1500000000"
    assert balances.model_dump()["pagination"] == {
        "next_cursor": None,
        "has_next_page": False,
    }
    assert objects.model_dump()["objects"][0]["object_id"] == "0x3"
    assert transaction.model_dump()["timestamp_ms"] == 1767323045000


def test_optional_provider_fields_can_be_omitted():
    balance = SuiCoinBalance(coin_type="0x2::sui::SUI", raw_balance="0")
    object_summary = SuiOwnedObject(object_id="0x2")
    transaction = SuiTransactionResponse(digest=VALID_DIGEST)

    assert balance.amount is None
    assert balance.decimals is None
    assert object_summary.version is None
    assert object_summary.digest is None
    assert transaction.timestamp_ms is None
    assert transaction.status is None


@pytest.mark.parametrize(
    "payload",
    [
        {"coin_type": "0x2::sui::SUI", "raw_balance": "-1"},
        {"coin_type": "0x2::sui::SUI", "raw_balance": "1.5"},
        {"coin_type": "0x2::sui::SUI", "raw_balance": "1", "amount": "-0.1"},
        {"coin_type": "0x2::sui::SUI", "raw_balance": 1},
        {"coin_type": "0x2::sui::SUI", "raw_balance": "1", "amount": 1.5},
        {"coin_type": "0x2::sui::SUI", "raw_balance": "1", "decimals": 256},
    ],
)
def test_coin_balance_rejects_malformed_provider_values(payload):
    with pytest.raises(ValidationError):
        SuiCoinBalance.model_validate(payload)


def test_readiness_rejects_negative_checkpoint_and_unknown_status():
    with pytest.raises(ValidationError):
        SuiHealthResponse(
            status="ready",
            configured=True,
            connected=True,
            network="mainnet",
            endpoint="https://sui-mainnet.gateway.tatum.io",
            latest_checkpoint=-1,
        )
    with pytest.raises(ValidationError):
        SuiHealthResponse(
            status="unknown",
            configured=True,
            connected=False,
            network="mainnet",
            endpoint="https://sui-mainnet.gateway.tatum.io",
        )


def test_sui_error_response_matches_existing_operation_error_convention():
    response = SuiErrorResponse(
        error="provider_unavailable",
        message="Sui provider is currently unavailable.",
    )
    assert response.model_dump() == {
        "error": "provider_unavailable",
        "message": "Sui provider is currently unavailable.",
        "operation_id": None,
    }
