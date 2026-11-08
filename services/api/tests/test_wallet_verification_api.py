"""Tests for server-side Sui wallet verification challenge and linkage."""
import base64
import hashlib
import json
import subprocess
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.wallet import router as wallet_router
from app.api.v1 import wallet as wallet_api
from app.core.auth import provision_user
from app.core.config import settings
from app.core.database import Base, _enable_sqlite_foreign_keys, get_db
from app.core.rate_limit import SensitiveEndpointRateLimiter
from app.main import handle_http_error
from app.models.domain import (
    AnonymousVisitorCredential,
    WalletAssociation,
    WalletVerificationChallenge,
)
from app.services.anonymous_visitor_identity import (
    provision_visitor_identity,
    resolve_visitor_credential,
)
from app.services import wallet_verification
from app.services.wallet_verification import (
    WalletAssociationError,
    WalletVerificationUnavailableError,
    _challenge_message,
    normalize_sui_address,
    resolve_verified_visitor_wallet,
    verify_personal_message_signature,
)

_VERIFIER_ROOT = Path(__file__).resolve().parents[1] / "wallet-verifier"
_FRONTEND_ORIGIN = "http://localhost:3000"


def _derive_wallet_address(seed: int) -> str:
    secret = bytes((seed + index) % 256 for index in range(32))
    script = (
        "import { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519';\n"
        f"const secret = Uint8Array.from({json.dumps(list(secret))});\n"
        "const keypair = Ed25519Keypair.fromSecretKey(secret);\n"
        "process.stdout.write(JSON.stringify({ address: keypair.toSuiAddress() }));\n"
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_VERIFIER_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)["address"]


def _sign_message(message: bytes, seed: int) -> tuple[str, str]:
    secret = bytes((seed + index) % 256 for index in range(32))
    script = (
        "import { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519';\n"
        f"const secret = Uint8Array.from({json.dumps(list(secret))});\n"
        f"const msg = Uint8Array.from({json.dumps(list(message))});\n"
        "const keypair = Ed25519Keypair.fromSecretKey(secret);\n"
        "const signed = await keypair.signPersonalMessage(msg);\n"
        "process.stdout.write(JSON.stringify({ address: keypair.toSuiAddress(), signature: signed.signature }));\n"
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_VERIFIER_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    return payload["address"], payload["signature"]


def _sign_with_other_key(message: bytes, expected_address: str, seed: int = 11) -> tuple[str, str]:
    secret = bytes((seed + index) % 256 for index in range(32))
    script = (
        "import { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519';\n"
        f"const secret = Uint8Array.from({json.dumps(list(secret))});\n"
        f"const msg = Uint8Array.from({json.dumps(list(message))});\n"
        "const keypair = Ed25519Keypair.fromSecretKey(secret);\n"
        "const signed = await keypair.signPersonalMessage(msg);\n"
        "process.stdout.write(JSON.stringify({ address: keypair.toSuiAddress(), signature: signed.signature }));\n"
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_VERIFIER_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["address"] != expected_address, (payload["address"], expected_address)
    return payload["address"], payload["signature"]


@pytest.fixture
async def wallet_harness(tmp_path):
    wallet_api._wallet_challenge_limiter = SensitiveEndpointRateLimiter(
        requests_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE,
    )
    wallet_api._wallet_verification_limiter = SensitiveEndpointRateLimiter(
        requests_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE,
    )
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'wallet.db'}",
        connect_args={"timeout": 10},
    )
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_db():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[get_db] = override_db
    app.add_exception_handler(HTTPException, handle_http_error)
    app.include_router(wallet_router, prefix="/api/v1")

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Origin": _FRONTEND_ORIGIN},
        ) as client:
            yield SimpleNamespace(
                client=client,
                factory=session_factory,
                app=app,
                engine=engine,
            )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def _provision_visitor(harness):
    async with harness.factory() as session:
        return await provision_visitor_identity(session)


async def _provision_user(harness):
    async with harness.factory() as session:
        return await provision_user(session)


def _hold_signatures_until_concurrent(monkeypatch, count=2):
    original = wallet_verification.verify_personal_message_signature
    ready = asyncio.Event()
    verified_count = 0

    async def verify_and_wait(message, signature, address):
        nonlocal verified_count
        is_valid = await original(message, signature, address)
        if is_valid:
            verified_count += 1
            if verified_count == count:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=20)
        return is_valid

    monkeypatch.setattr(
        wallet_verification,
        "verify_personal_message_signature",
        verify_and_wait,
    )


def _visitor_headers(visitor):
    return {"Authorization": f"Bearer {visitor.credential}"}


def test_sui_address_canonicalization_pads_short_hex_addresses():
    assert normalize_sui_address("0x1") == "0x" + ("0" * 63) + "1"


async def _issue_and_sign(harness, visitor, seed):
    address = _derive_wallet_address(seed)
    challenge = await harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address},
        headers=_visitor_headers(visitor),
    )
    assert challenge.status_code == 200
    message = base64.b64decode(challenge.json()["message"])
    _, signature = _sign_message(message, seed)
    return challenge.json()["challenge_id"], signature


@pytest.mark.asyncio
async def test_wallet_challenge_creation_and_message_binding(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    address = _derive_wallet_address(7)

    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["purpose"] == "wallet_link"
    assert payload["network"] == "mainnet"
    assert payload["chain"] == "sui"
    assert payload["address"] == address
    assert set(payload) == {"challenge_id", "purpose", "chain", "network", "address", "message", "expires_at"}
    assert "signature" not in response.text
    assert "orv_" not in response.text

    message = base64.b64decode(payload["message"])
    async with wallet_harness.factory() as session:
        record = await session.get(WalletVerificationChallenge, payload["challenge_id"])
        assert record is not None
        assert record.message_digest == hashlib.sha256(message).hexdigest()
        assert record.canonical_address == address
        message_payload = json.loads(message)
        assert len(message_payload["nonce"]) == 32
        assert all(character in "0123456789abcdef" for character in message_payload["nonce"])
        assert message_payload["origin"] == _FRONTEND_ORIGIN
        assert message_payload["domain"] == "localhost:3000"
        stored_expires_at = record.expires_at
        if stored_expires_at.tzinfo is None:
            stored_expires_at = stored_expires_at.replace(tzinfo=timezone.utc)
        assert message_payload["expires_at"] == stored_expires_at.isoformat().replace("+00:00", "Z")
        assert _challenge_message(record, origin=_FRONTEND_ORIGIN) == message


@pytest.mark.asyncio
async def test_wallet_challenge_expiry_rejected(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    address = _derive_wallet_address(7)
    challenge = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    challenge_id = challenge.json()["challenge_id"]

    async with wallet_harness.factory() as session:
        record = await session.get(WalletVerificationChallenge, challenge_id)
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    _, signature = _sign_message(base64.b64decode(challenge.json()["message"]), 7)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "wallet_verification_failed"


@pytest.mark.asyncio
async def test_wallet_signature_verification_success_and_replay(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    address = _derive_wallet_address(7)
    challenge = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    message = base64.b64decode(challenge.json()["message"])
    _, signature = _sign_message(message, 7)

    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge.json()["challenge_id"], "signature": signature},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "verified"
    assert body["address"] == address
    assert body["network"] == "mainnet"

    replay = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge.json()["challenge_id"], "signature": signature},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    assert replay.status_code == 409
    assert replay.json()["code"] == "wallet_verification_failed"


@pytest.mark.asyncio
async def test_verified_wallet_resolution_requires_exact_user_and_visitor(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    another_visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(wallet_harness, visitor, 7)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers=_visitor_headers(visitor),
    )
    assert response.status_code == 200

    async with wallet_harness.factory() as session:
        own_association = await resolve_verified_visitor_wallet(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
        )
        assert own_association is not None
        assert own_association.canonical_address == response.json()["address"]

        assert await resolve_verified_visitor_wallet(
            session,
            user_id=another_visitor.user.id,
            visitor_identity_id=another_visitor.identity.id,
        ) is None
        assert await resolve_verified_visitor_wallet(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=another_visitor.identity.id,
        ) is None
        assert await resolve_verified_visitor_wallet(
            session,
            user_id=another_visitor.user.id,
            visitor_identity_id=visitor.identity.id,
        ) is None


@pytest.mark.asyncio
async def test_expired_visitor_credential_cannot_verify_or_access_wallet_link(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(wallet_harness, visitor, 7)
    verified = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers=_visitor_headers(visitor),
    )
    assert verified.status_code == 200

    async with wallet_harness.factory() as session:
        credential = await session.scalar(
            select(AnonymousVisitorCredential).where(
                AnonymousVisitorCredential.visitor_id == visitor.identity.id
            )
        )
        credential.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    denied = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={
            "purpose": "wallet_link",
            "network": "mainnet",
            "address": verified.json()["address"],
        },
        headers=_visitor_headers(visitor),
    )
    assert denied.status_code == 401


@pytest.mark.asyncio
async def test_unverified_address_cannot_resolve_and_revoked_association_is_rejected(
    wallet_harness,
):
    visitor = await _provision_visitor(wallet_harness)
    address = _derive_wallet_address(7)
    challenge = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address},
        headers=_visitor_headers(visitor),
    )
    assert challenge.status_code == 200

    async with wallet_harness.factory() as session:
        assert await resolve_verified_visitor_wallet(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
        ) is None

    message = base64.b64decode(challenge.json()["message"])
    _, signature = _sign_message(message, 7)
    verified = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge.json()["challenge_id"], "signature": signature},
        headers=_visitor_headers(visitor),
    )
    assert verified.status_code == 200

    async with wallet_harness.factory() as session:
        association = await session.scalar(select(WalletAssociation))
        association.revoked_at = datetime.now(timezone.utc)
        await session.commit()
        assert await resolve_verified_visitor_wallet(
            session,
            user_id=visitor.user.id,
            visitor_identity_id=visitor.identity.id,
        ) is None


@pytest.mark.asyncio
async def test_verified_wallet_network_must_match_tatum_configuration(
    wallet_harness,
    monkeypatch,
):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(wallet_harness, visitor, 7)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers=_visitor_headers(visitor),
    )
    assert response.status_code == 200

    monkeypatch.setattr(settings, "SUI_NETWORK", "testnet")
    async with wallet_harness.factory() as session:
        with pytest.raises(WalletAssociationError, match="configured Sui network"):
            await resolve_verified_visitor_wallet(
                session,
                user_id=visitor.user.id,
                visitor_identity_id=visitor.identity.id,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [("chain", "ethereum"), ("network", "testnet")],
)
async def test_invalid_verified_wallet_chain_or_network_is_rejected(
    wallet_harness,
    monkeypatch,
    field,
    value,
):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(wallet_harness, visitor, 7)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers=_visitor_headers(visitor),
    )
    assert response.status_code == 200

    monkeypatch.setattr(settings, "SUI_NETWORK", "mainnet")
    async with wallet_harness.factory() as session:
        association = await session.scalar(select(WalletAssociation))
        setattr(association, field, value)
        await session.commit()
        with pytest.raises(WalletAssociationError):
            await resolve_verified_visitor_wallet(
                session,
                user_id=visitor.user.id,
                visitor_identity_id=visitor.identity.id,
            )


@pytest.mark.asyncio
async def test_wallet_invalid_signature_wrong_address_and_network_rejected(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    address = _derive_wallet_address(7)
    challenge = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    message = base64.b64decode(challenge.json()["message"])
    _, wrong_signature = _sign_with_other_key(message, address)
    invalid = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge.json()["challenge_id"], "signature": wrong_signature},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    assert invalid.status_code == 409
    async with wallet_harness.factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(WalletAssociation)
        ) == 0
        assert await resolve_visitor_credential(session, visitor.credential) is not None

    other_address = _derive_wallet_address(11)
    challenge_2 = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": other_address},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    assert challenge_2.status_code == 200, challenge_2.text
    message_2 = base64.b64decode(challenge_2.json()["message"])
    wrong_network_message = message_2.replace(
        b'"network":"mainnet"',
        b'"network":"testnet"',
    )
    _, wrong_network_signature = _sign_message(wrong_network_message, 11)
    wrong_network_response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={
            "challenge_id": challenge_2.json()["challenge_id"],
            "signature": wrong_network_signature,
        },
        headers=_visitor_headers(visitor),
    )
    assert wrong_network_response.status_code == 409, wrong_network_response.text
    _, signature = _sign_message(message_2, 11)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_2.json()["challenge_id"], "signature": signature},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    assert response.status_code == 200

    wrong_network_message = message_2.replace(b'"network":"mainnet"', b'"network":"testnet"')
    _, wrong_network_signature = _sign_message(wrong_network_message, 11)
    response_3 = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_2.json()["challenge_id"], "signature": wrong_network_signature},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    assert response_3.status_code == 409


@pytest.mark.asyncio
async def test_wallet_challenge_rejects_client_owned_fields_and_pats(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    address = _derive_wallet_address(7)
    _, pat_token = await _provision_user(wallet_harness)

    challenge = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address, "user_id": "evil", "visitor_id": "evil", "namespace": "evil"},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    assert challenge.status_code == 422

    pat_response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address},
        headers={"Authorization": f"Bearer {pat_token}"},
    )
    assert pat_response.status_code == 401


@pytest.mark.asyncio
async def test_wallet_link_conflict_requires_distinct_visitor_identities(wallet_harness):
    first = await _provision_visitor(wallet_harness)
    second = await _provision_visitor(wallet_harness)
    address = _derive_wallet_address(7)

    first_challenge = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address},
        headers={"Authorization": f"Bearer {first.credential}"},
    )
    first_message = base64.b64decode(first_challenge.json()["message"])
    _, first_signature = _sign_message(first_message, 7)
    first_verified = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": first_challenge.json()["challenge_id"], "signature": first_signature},
        headers={"Authorization": f"Bearer {first.credential}"},
    )
    assert first_verified.status_code == 200

    second_challenge = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address},
        headers={"Authorization": f"Bearer {second.credential}"},
    )
    second_message = base64.b64decode(second_challenge.json()["message"])
    _, second_signature = _sign_message(second_message, 7)
    second_verified = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": second_challenge.json()["challenge_id"], "signature": second_signature},
        headers={"Authorization": f"Bearer {second.credential}"},
    )
    assert second_verified.status_code == 409
    assert second_verified.json()["code"] == "wallet_verification_failed"
    assert "another identity" not in second_verified.text.lower()


@pytest.mark.asyncio
async def test_wallet_challenge_prevents_duplicate_active_wallet_per_visitor(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    first_address = _derive_wallet_address(7)
    first = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": first_address},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    first_message = base64.b64decode(first.json()["message"])
    _, first_signature = _sign_message(first_message, 7)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": first.json()["challenge_id"], "signature": first_signature},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    assert response.status_code == 200

    second_address = _derive_wallet_address(17)
    second = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": second_address},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    second_message = base64.b64decode(second.json()["message"])
    _, second_signature = _sign_message(second_message, 17)
    second_response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": second.json()["challenge_id"], "signature": second_signature},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    assert second_response.status_code == 409
    assert second_response.json()["code"] == "wallet_verification_failed"


@pytest.mark.asyncio
async def test_wallet_verification_does_not_expose_credentials_or_signatures(
    wallet_harness,
    caplog,
):
    visitor = await _provision_visitor(wallet_harness)
    address = _derive_wallet_address(7)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    message = base64.b64decode(response.json()["message"])
    _, signature = _sign_message(message, 7)

    verify = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": response.json()["challenge_id"], "signature": signature},
        headers={"Authorization": f"Bearer {visitor.credential}"},
    )
    assert verify.status_code == 200
    assert "signature" not in verify.text.lower()
    assert visitor.credential not in verify.text
    assert signature not in caplog.text
    assert visitor.credential not in caplog.text


@pytest.mark.asyncio
async def test_concurrent_consumption_of_one_challenge_succeeds_once(wallet_harness, monkeypatch):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(wallet_harness, visitor, 7)
    _hold_signatures_until_concurrent(monkeypatch)

    async def consume():
        return await wallet_harness.client.post(
            "/api/v1/wallet/verify-signature",
            json={"challenge_id": challenge_id, "signature": signature},
            headers=_visitor_headers(visitor),
        )

    responses = await asyncio.gather(consume(), consume())
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["code"] == "wallet_verification_failed"

    async with wallet_harness.factory() as session:
        associations = await session.scalar(
            select(func.count()).select_from(WalletAssociation)
        )
        challenges = await session.execute(
            select(WalletVerificationChallenge).where(
                WalletVerificationChallenge.id == challenge_id
            )
        )
        record = challenges.scalar_one()
        assert associations == 1
        assert record.consumed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "same_wallet",
    [True, False],
    ids=["wallet-cannot-cross-identities", "identity-cannot-link-two-wallets"],
)
async def test_concurrent_wallet_association_uniqueness(
    wallet_harness,
    monkeypatch,
    same_wallet,
):
    first_visitor = await _provision_visitor(wallet_harness)
    second_visitor = (
        await _provision_visitor(wallet_harness) if same_wallet else first_visitor
    )
    first_challenge = await _issue_and_sign(wallet_harness, first_visitor, 7)
    second_challenge = await _issue_and_sign(
        wallet_harness,
        second_visitor,
        7 if same_wallet else 17,
    )
    _hold_signatures_until_concurrent(monkeypatch)

    async def consume(visitor, signed_challenge):
        challenge_id, signature = signed_challenge
        return await wallet_harness.client.post(
            "/api/v1/wallet/verify-signature",
            json={"challenge_id": challenge_id, "signature": signature},
            headers=_visitor_headers(visitor),
        )

    responses = await asyncio.gather(
        consume(first_visitor, first_challenge),
        consume(second_visitor, second_challenge),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["code"] == "wallet_verification_failed"
    assert "already linked" not in conflict.text.lower()

    async with wallet_harness.factory() as session:
        associations = await session.scalar(
            select(func.count()).select_from(WalletAssociation).where(
                WalletAssociation.revoked_at.is_(None)
            )
        )
        assert associations == 1


@pytest.mark.asyncio
async def test_association_insert_failure_rolls_back_challenge_consumption(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(wallet_harness, visitor, 7)
    async with wallet_harness.engine.begin() as connection:
        await connection.exec_driver_sql(
            "CREATE TRIGGER reject_wallet_association BEFORE INSERT "
            "ON wallet_associations BEGIN "
            "SELECT RAISE(ABORT, 'forced association failure'); END"
        )

    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers=_visitor_headers(visitor),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "wallet_verification_failed"
    assert "forced association failure" not in response.text

    async with wallet_harness.factory() as session:
        challenge = await session.get(WalletVerificationChallenge, challenge_id)
        association_count = await session.scalar(
            select(func.count()).select_from(WalletAssociation)
        )
        assert challenge.consumed_at is None
        assert association_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("address", "0x" + "1" * 64),
        ("network", "testnet"),
        ("chain", "other-chain"),
        ("purpose", "wallet_recovery"),
        ("visitor_identity_id", "another-visitor"),
        ("user_id", "another-user"),
        ("origin", "http://127.0.0.1:3000"),
        ("expires_at", "2099-01-01T00:00:00Z"),
    ],
)
async def test_signature_is_bound_to_all_challenge_context(
    wallet_harness,
    field,
    replacement,
):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, _ = await _issue_and_sign(wallet_harness, visitor, 7)
    async with wallet_harness.factory() as session:
        record = await session.get(WalletVerificationChallenge, challenge_id)
        signed_message = json.loads(
            _challenge_message(record, origin=_FRONTEND_ORIGIN)
        )

    signed_message[field] = replacement
    altered_bytes = json.dumps(
        signed_message,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    _, altered_signature = _sign_message(altered_bytes, 7)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": altered_signature},
        headers=_visitor_headers(visitor),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "wallet_verification_failed"

    async with wallet_harness.factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(WalletAssociation)
        ) == 0


@pytest.mark.asyncio
async def test_challenge_isolated_to_issuing_visitor(wallet_harness):
    first_visitor = await _provision_visitor(wallet_harness)
    second_visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(wallet_harness, first_visitor, 7)

    wrong_visitor = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers=_visitor_headers(second_visitor),
    )
    assert wrong_visitor.status_code == 409

    owner_verification = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers=_visitor_headers(first_visitor),
    )
    assert owner_verification.status_code == 200


@pytest.mark.asyncio
async def test_revoked_challenge_cannot_be_consumed(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(wallet_harness, visitor, 7)
    async with wallet_harness.factory() as session:
        record = await session.get(WalletVerificationChallenge, challenge_id)
        record.revoked_at = datetime.now(timezone.utc)
        await session.commit()

    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers=_visitor_headers(visitor),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "wallet_verification_failed"


@pytest.mark.asyncio
async def test_wallet_endpoints_enforce_direct_peer_rate_limits(wallet_harness, monkeypatch):
    visitor = await _provision_visitor(wallet_harness)
    monkeypatch.setattr(
        wallet_api,
        "_wallet_challenge_limiter",
        SensitiveEndpointRateLimiter(requests_per_minute=1),
    )
    challenge = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={
            "purpose": "wallet_link",
            "network": "mainnet",
            "address": _derive_wallet_address(7),
        },
        headers=_visitor_headers(visitor),
    )
    assert challenge.status_code == 200
    blocked_challenge = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={
            "purpose": "wallet_link",
            "network": "mainnet",
            "address": _derive_wallet_address(11),
        },
        headers={
            **_visitor_headers(visitor),
            "X-Forwarded-For": "203.0.113.1",
        },
    )
    assert blocked_challenge.status_code == 429
    assert blocked_challenge.headers["retry-after"]

    signature = _sign_message(
        base64.b64decode(challenge.json()["message"]),
        7,
    )[1]
    monkeypatch.setattr(
        wallet_api,
        "_wallet_verification_limiter",
        SensitiveEndpointRateLimiter(requests_per_minute=1),
    )
    verification = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={
            "challenge_id": challenge.json()["challenge_id"],
            "signature": signature,
        },
        headers=_visitor_headers(visitor),
    )
    assert verification.status_code == 200
    blocked_verification = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={
            "challenge_id": challenge.json()["challenge_id"],
            "signature": signature,
        },
        headers={
            **_visitor_headers(visitor),
            "X-Forwarded-For": "203.0.113.2",
        },
    )
    assert blocked_verification.status_code == 429


@pytest.mark.asyncio
async def test_verifier_runtime_failure_is_reported_as_unavailable(monkeypatch):
    async def missing_node(*_args, **_kwargs):
        raise FileNotFoundError("node")

    monkeypatch.setattr(wallet_verification.asyncio, "create_subprocess_exec", missing_node)
    with pytest.raises(WalletVerificationUnavailableError):
        await verify_personal_message_signature(b"message", "signature", "0x1")


@pytest.mark.asyncio
async def test_verifier_timeout_kills_child_process(monkeypatch):
    class FakeStdin:
        def write(self, _value):
            return None

        async def drain(self):
            return None

        def close(self):
            return None

    class StalledStream:
        async def read(self, _size):
            await asyncio.Future()

    class StalledProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = StalledStream()
            self.returncode = None
            self.killed = False

        def kill(self):
            self.killed = True

        async def wait(self):
            if not self.killed:
                await asyncio.Future()
            self.returncode = -9
            return self.returncode

    process = StalledProcess()

    async def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(wallet_verification.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(wallet_verification, "_VERIFIER_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(WalletVerificationUnavailableError, match="timed out"):
        await verify_personal_message_signature(b"message", "signature", "0x1")
    assert process.killed


@pytest.mark.asyncio
async def test_verifier_rejects_oversized_output(monkeypatch):
    class FakeStdin:
        def write(self, _value):
            return None

        async def drain(self):
            return None

        def close(self):
            return None

    class OversizedStream:
        async def read(self, _size):
            return b"x" * 513

    class OversizedProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = OversizedStream()
            self.returncode = 0
            self.killed = False

        def kill(self):
            self.killed = True

        async def wait(self):
            return self.returncode

    process = OversizedProcess()

    async def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(wallet_verification.asyncio, "create_subprocess_exec", create_process)
    with pytest.raises(WalletVerificationUnavailableError, match="invalid response"):
        await verify_personal_message_signature(b"message", "signature", "0x1")
