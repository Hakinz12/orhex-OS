"""Tests for server-side Sui wallet verification challenge and linkage."""
import base64
import hashlib
import json
import subprocess
import asyncio
import logging
import threading
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


def _wallet_standard_sign(message: bytes, seed: int) -> dict[str, str]:
    secret = bytes((seed + index) % 256 for index in range(32))
    script = (
        "import { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519';\n"
        f"const secret = Uint8Array.from({json.dumps(list(secret))});\n"
        f"const message = Uint8Array.from({json.dumps(list(message))});\n"
        "const keypair = Ed25519Keypair.fromSecretKey(secret);\n"
        "const signed = await keypair.signPersonalMessage(message);\n"
        "process.stdout.write(JSON.stringify({ address: keypair.toSuiAddress(), ...signed }));\n"
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
    return json.loads(completed.stdout)


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

    async def verify_and_wait(
        message,
        signature,
        address,
        *,
        network: str = "mainnet",
        correlation_id: str | None = None,
    ):
        nonlocal verified_count
        is_valid = await original(
            message,
            signature,
            address,
            network=network,
            correlation_id=correlation_id,
        )
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


async def _issue_and_sign(harness, visitor, seed, network="mainnet"):
    address = _derive_wallet_address(seed)
    challenge = await harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": network, "address": address},
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
        assert (record.expires_at - record.created_at).total_seconds() == 600
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
async def test_devnet_wallet_verification_is_explicitly_unsupported(
    wallet_harness,
    monkeypatch,
):
    monkeypatch.setattr(settings, "SUI_NETWORK", "devnet")
    visitor = await _provision_visitor(wallet_harness)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={
            "purpose": "wallet_link",
            "network": "devnet",
            "address": _derive_wallet_address(7),
        },
        headers=_visitor_headers(visitor),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_wallet_request"
    async with wallet_harness.factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(WalletVerificationChallenge)
        ) == 0


@pytest.mark.asyncio
async def test_verifier_receives_network_from_stored_challenge(wallet_harness, monkeypatch):
    monkeypatch.setattr(settings, "SUI_NETWORK", "testnet")
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(
        wallet_harness,
        visitor,
        27,
        network="testnet",
    )
    captured_request = {}

    def fake_verifier(request_bytes, _cancellation_requested=None):
        captured_request.update(json.loads(request_bytes))
        return (
            json.dumps(
                {
                    "ok": True,
                    "address": captured_request["address"],
                    "scheme": "ED25519",
                }
            ).encode("utf-8"),
            0,
        )

    monkeypatch.setattr(wallet_verification, "_run_wallet_verifier", fake_verifier)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers=_visitor_headers(visitor),
    )

    assert response.status_code == 200
    assert captured_request["network"] == "testnet"
    async with wallet_harness.factory() as session:
        challenge = await session.get(WalletVerificationChallenge, challenge_id)
        expected_message = _challenge_message(challenge, origin=_FRONTEND_ORIGIN)
    assert base64.b64decode(captured_request["message"], validate=True) == expected_message
    assert captured_request["signature"] == signature
    assert captured_request["address"] == response.json()["address"]


@pytest.mark.asyncio
async def test_verifier_service_failure_is_a_sanitized_api_503(
    wallet_harness,
    monkeypatch,
    caplog,
):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(wallet_harness, visitor, 28)

    def failed_verifier(_request_bytes, _cancellation_requested=None):
        return (
            json.dumps(
                {
                    "ok": False,
                    "code": "verification_service_error",
                    "scheme": "ED25519",
                }
            ).encode("utf-8"),
            0,
        )

    monkeypatch.setattr(wallet_verification, "_run_wallet_verifier", failed_verifier)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers=_visitor_headers(visitor),
    )

    assert response.status_code == 503
    assert response.json()["code"] == "wallet_verification_unavailable"
    assert signature not in response.text
    assert signature not in caplog.text
    assert "stage=verification_service code=wallet_verification_unavailable scheme=ED25519" in caplog.text
    async with wallet_harness.factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(WalletAssociation)
        ) == 0


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
    assert response.json()["code"] == "wallet_challenge_expired"


@pytest.mark.asyncio
async def test_wallet_signature_verification_success_and_replay(
    wallet_harness,
    monkeypatch,
):
    original_verifier = wallet_verification.verify_personal_message_signature
    verifier_completed = False

    async def tracked_verifier(*args, **kwargs):
        nonlocal verifier_completed
        result = await original_verifier(*args, **kwargs)
        verifier_completed = True
        return result

    monkeypatch.setattr(
        wallet_verification,
        "verify_personal_message_signature",
        tracked_verifier,
    )
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
    assert verifier_completed
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
    assert replay.json()["code"] == "wallet_challenge_consumed"


@pytest.mark.asyncio
async def test_signed_message_mismatch_does_not_consume_challenge_and_new_try_succeeds(
    wallet_harness,
):
    visitor = await _provision_visitor(wallet_harness)
    address = _derive_wallet_address(7)
    challenge = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address},
        headers=_visitor_headers(visitor),
    )
    message = base64.b64decode(challenge.json()["message"])
    _, signature = _sign_message(message, 7)
    mismatch = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={
            "challenge_id": challenge.json()["challenge_id"],
            "signature": signature,
            "signed_message": base64.b64encode(b"a different message").decode("ascii"),
        },
        headers=_visitor_headers(visitor),
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "wallet_signed_message_mismatch"

    async with wallet_harness.factory() as session:
        record = await session.get(
            WalletVerificationChallenge,
            challenge.json()["challenge_id"],
        )
        assert record.consumed_at is None

    retry = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={
            "challenge_id": challenge.json()["challenge_id"],
            "signature": signature,
            "signed_message": challenge.json()["message"],
        },
        headers=_visitor_headers(visitor),
    )
    assert retry.status_code == 200
    assert retry.json()["status"] == "verified"


@pytest.mark.asyncio
async def test_invalid_signature_encoding_has_a_distinct_safe_error(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, _signature = await _issue_and_sign(wallet_harness, visitor, 7)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": "!", "signed_message": "YQ=="},
        headers=_visitor_headers(visitor),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "wallet_signature_encoding_invalid"
    assert "!" not in response.text


@pytest.mark.asyncio
async def test_real_wallet_standard_sdk_signature_and_returned_bytes_verify():
    message = b"ORHEX Sui ownership verification test"
    signed = _wallet_standard_sign(message, 21)
    assert base64.b64decode(signed["bytes"], validate=True) == message
    assert await verify_personal_message_signature(
        message,
        signed["signature"],
        signed["address"],
        network="mainnet",
    )


@pytest.mark.asyncio
async def test_sdk_signature_failure_categories_are_distinguished():
    message = b"ORHEX Sui ownership verification test"
    signed = _wallet_standard_sign(message, 22)
    with pytest.raises(wallet_verification.WalletDerivedAddressMismatchError):
        await verify_personal_message_signature(
            message,
            signed["signature"],
            _derive_wallet_address(23),
            network="mainnet",
        )
    with pytest.raises(wallet_verification.InvalidWalletSignatureError):
        await verify_personal_message_signature(
            message + b" altered",
            signed["signature"],
            signed["address"],
            network="mainnet",
        )
    with pytest.raises(wallet_verification.InvalidWalletSignatureEncodingError):
        await verify_personal_message_signature(
            message,
            "!",
            signed["address"],
            network="mainnet",
        )
    with pytest.raises(wallet_verification.WalletSignatureMissingError):
        await verify_personal_message_signature(
            message,
            "",
            signed["address"],
            network="mainnet",
        )
    with pytest.raises(wallet_verification.UnsupportedWalletSignatureSchemeError):
        await verify_personal_message_signature(
            message,
            base64.b64encode(b"\x04unsupported").decode("ascii"),
            signed["address"],
            network="mainnet",
        )
    malformed_payload = base64.b64encode(b"\x00" + bytes(8)).decode("ascii")
    with pytest.raises(wallet_verification.InvalidWalletSignatureEncodingError):
        await verify_personal_message_signature(
            message,
            malformed_payload,
            signed["address"],
            network="mainnet",
        )


@pytest.mark.asyncio
async def test_wallet_standard_returned_bytes_must_match_challenge(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(wallet_harness, visitor, 24)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={
            "challenge_id": challenge_id,
            "signature": signature,
            "signed_message": base64.b64encode(b"not the challenge").decode("ascii"),
        },
        headers=_visitor_headers(visitor),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "wallet_signed_message_mismatch"


@pytest.mark.asyncio
async def test_missing_signature_has_a_distinct_safe_error(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, _signature = await _issue_and_sign(wallet_harness, visitor, 7)
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id},
        headers=_visitor_headers(visitor),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "wallet_signature_missing"


@pytest.mark.asyncio
async def test_disconnect_revokes_wallet_association_and_wallet_status(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(wallet_harness, visitor, 7)
    verified = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers=_visitor_headers(visitor),
    )
    assert verified.status_code == 200

    disconnected = await wallet_harness.client.delete(
        "/api/v1/wallet/verified",
        headers=_visitor_headers(visitor),
    )
    assert disconnected.status_code == 200
    assert disconnected.json()["verified"] is False
    status = await wallet_harness.client.get(
        "/api/v1/wallet/verified",
        headers=_visitor_headers(visitor),
    )
    assert status.json()["verified"] is False
    async with wallet_harness.factory() as session:
        association = await session.scalar(select(WalletAssociation))
        assert association.revoked_at is not None


@pytest.mark.asyncio
async def test_disconnect_revokes_pending_challenge_before_it_can_link(wallet_harness):
    visitor = await _provision_visitor(wallet_harness)
    address = _derive_wallet_address(7)
    challenge = await wallet_harness.client.post(
        "/api/v1/wallet/verify-challenge",
        json={"purpose": "wallet_link", "network": "mainnet", "address": address},
        headers=_visitor_headers(visitor),
    )
    message = base64.b64decode(challenge.json()["message"])
    _, signature = _sign_message(message, 7)

    disconnected = await wallet_harness.client.delete(
        "/api/v1/wallet/verified",
        headers=_visitor_headers(visitor),
    )
    assert disconnected.status_code == 200
    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge.json()["challenge_id"], "signature": signature},
        headers=_visitor_headers(visitor),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "wallet_challenge_not_found"
    async with wallet_harness.factory() as session:
        assert await session.scalar(select(func.count()).select_from(WalletAssociation)) == 0


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
async def test_wallet_invalid_signature_wrong_address_and_network_rejected(
    wallet_harness,
    monkeypatch,
):
    original_verifier = wallet_verification.verify_personal_message_signature
    verifier_started = False

    async def tracked_verifier(*args, **kwargs):
        nonlocal verifier_started
        verifier_started = True
        return await original_verifier(*args, **kwargs)

    monkeypatch.setattr(
        wallet_verification,
        "verify_personal_message_signature",
        tracked_verifier,
    )
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
    assert invalid.json()["code"] == "wallet_derived_address_mismatch"
    assert verifier_started
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
    assert second_verified.json()["code"] == "wallet_already_linked"
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
    assert second_response.json()["code"] == "wallet_already_linked"


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
async def test_unexpected_verification_error_logs_traceback_and_returns_sanitized_response(
    wallet_harness,
    monkeypatch,
    caplog,
):
    visitor = await _provision_visitor(wallet_harness)
    challenge_id, signature = await _issue_and_sign(wallet_harness, visitor, 7)
    correlation_id = "cbac6708-d13b-4765-90e9-73d6e24f07b2"

    async def fail_unexpectedly(*_args, **_kwargs):
        raise NotImplementedError("diagnostic-only exception marker")

    monkeypatch.setattr(wallet_api, "consume_wallet_challenge", fail_unexpectedly)
    monkeypatch.setattr(wallet_api, "_correlation_id", lambda _request: correlation_id)
    caplog.set_level(logging.ERROR, logger="orhex.api.wallet")

    response = await wallet_harness.client.post(
        "/api/v1/wallet/verify-signature",
        json={"challenge_id": challenge_id, "signature": signature},
        headers=_visitor_headers(visitor),
    )

    assert response.status_code == 500
    assert response.json()["error"] == "wallet_verification_internal_error"
    assert response.json()["code"] == "wallet_verification_internal_error"
    assert response.json()["message"] == (
        "Wallet verification could not be completed due to an internal error."
    )
    assert "diagnostic-only exception marker" not in response.text
    assert signature not in response.text
    assert visitor.credential not in response.text
    assert f"correlation_id={correlation_id} stage=internal_error" in caplog.text
    assert "Traceback (most recent call last)" in caplog.text
    assert "NotImplementedError: diagnostic-only exception marker" in caplog.text


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
    assert conflict.json()["code"] in {
        "wallet_challenge_consumed",
        "wallet_already_linked",
    }

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
    assert conflict.json()["code"] in {
        "wallet_challenge_consumed",
        "wallet_already_linked",
    }

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

    expired_attribute_loads = []

    def record_expired_attribute_load(execute_state):
        if execute_state.is_column_load:
            expired_attribute_loads.append(execute_state.statement)

    event.listen(
        AsyncSession.sync_session_class,
        "do_orm_execute",
        record_expired_attribute_load,
    )
    try:
        response = await wallet_harness.client.post(
            "/api/v1/wallet/verify-signature",
            json={"challenge_id": challenge_id, "signature": signature},
            headers=_visitor_headers(visitor),
        )
    finally:
        event.remove(
            AsyncSession.sync_session_class,
            "do_orm_execute",
            record_expired_attribute_load,
        )
    assert response.status_code == 503
    assert response.json()["code"] == "wallet_verification_unavailable"
    assert "forced association failure" not in response.text
    assert expired_attribute_loads == []

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
    assert response.json()["code"] == "wallet_signature_verification_failed"

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
    assert response.json()["code"] == "wallet_challenge_not_found"


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
    def missing_node(*_args, **_kwargs):
        raise FileNotFoundError("node")

    monkeypatch.setattr(wallet_verification.subprocess, "Popen", missing_node)
    signature = base64.b64encode(b"signature").decode("ascii")
    with pytest.raises(WalletVerificationUnavailableError):
        await verify_personal_message_signature(b"message", signature, "0x1")


@pytest.mark.asyncio
async def test_verifier_timeout_terminates_and_reaps_child_process(monkeypatch):
    class FakeStdin:
        def __init__(self, killed):
            self.killed = killed
            self.closed = False

        def write(self, _value):
            self.killed.wait()
            return len(_value)

        def close(self):
            self.closed = True

    class StalledStream:
        def __init__(self, killed):
            self.killed = killed
            self.closed = False

        def read(self, _size):
            self.killed.wait()
            return b""

        def close(self):
            self.closed = True

    class StalledProcess:
        def __init__(self):
            self.killed_event = threading.Event()
            self.stdin = FakeStdin(self.killed_event)
            self.stdout = StalledStream(self.killed_event)
            self.returncode = None
            self.killed = False
            self.terminated = False
            self.reaped = False

        def terminate(self):
            self.terminated = True
            self.killed_event.set()
            self.returncode = -15

        def kill(self):
            self.killed = True
            self.killed_event.set()
            self.returncode = -9

        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("node", timeout)
            self.reaped = True
            return self.returncode

        def poll(self):
            return self.returncode

    process = StalledProcess()

    def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(wallet_verification.subprocess, "Popen", create_process)
    monkeypatch.setattr(wallet_verification, "_VERIFIER_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(WalletVerificationUnavailableError, match="timed out"):
        await verify_personal_message_signature(
            b"message",
            base64.b64encode(b"signature").decode("ascii"),
            "0x1",
        )
    assert process.terminated
    assert process.reaped
    assert process.stdin.closed
    assert process.stdout.closed


@pytest.mark.asyncio
async def test_verifier_deadline_includes_synchronous_process_creation(monkeypatch):
    class FakePipe:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class DelayedProcess:
        def __init__(self):
            self.stdin = FakePipe()
            self.stdout = FakePipe()
            self.returncode = None
            self.terminated = False
            self.reaped = False

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("node", timeout)
            self.reaped = True
            return self.returncode

        def poll(self):
            return self.returncode

    process = DelayedProcess()

    def delayed_process_creation(*_args, **_kwargs):
        threading.Event().wait(0.03)
        return process

    monkeypatch.setattr(
        wallet_verification.subprocess,
        "Popen",
        delayed_process_creation,
    )
    monkeypatch.setattr(wallet_verification, "_VERIFIER_TIMEOUT_SECONDS", 0.005)
    with pytest.raises(WalletVerificationUnavailableError, match="timed out"):
        await verify_personal_message_signature(
            b"message",
            base64.b64encode(b"signature").decode("ascii"),
            "0x1",
        )
    assert process.terminated
    assert process.reaped
    assert process.stdin.closed
    assert process.stdout.closed


@pytest.mark.asyncio
async def test_verifier_timeout_escalates_to_kill_and_reaps(monkeypatch):
    class FakePipe:
        def write(self, value):
            return len(value)

        def read(self, _size):
            return b""

        def close(self):
            return None

    class SlowToStopProcess:
        def __init__(self):
            self.stdin = FakePipe()
            self.stdout = FakePipe()
            self.returncode = None
            self.terminated = False
            self.killed = False
            self.reaped = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("node", timeout)
            self.reaped = True
            return self.returncode

        def poll(self):
            return self.returncode

    process = SlowToStopProcess()
    monkeypatch.setattr(
        wallet_verification.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(wallet_verification, "_VERIFIER_TIMEOUT_SECONDS", 0.005)
    monkeypatch.setattr(
        wallet_verification,
        "_VERIFIER_TERMINATE_GRACE_SECONDS",
        0.001,
    )
    with pytest.raises(WalletVerificationUnavailableError, match="timed out"):
        await verify_personal_message_signature(
            b"message",
            base64.b64encode(b"signature").decode("ascii"),
            "0x1",
        )
    assert process.terminated
    assert process.killed
    assert process.reaped


@pytest.mark.asyncio
async def test_verifier_process_does_not_block_the_event_loop(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_verifier(_request, _cancellation_requested=None):
        started.set()
        release.wait()
        return (
            json.dumps({"ok": True, "address": "0x1"}).encode("utf-8"),
            0,
        )

    async def wait_until_started():
        for _ in range(100):
            if started.is_set():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("The verifier worker did not start.")

    monkeypatch.setattr(
        wallet_verification,
        "_run_wallet_verifier",
        blocking_verifier,
    )
    verification = asyncio.create_task(
        verify_personal_message_signature(
            b"message",
            base64.b64encode(b"signature").decode("ascii"),
            "0x1",
        )
    )
    try:
        await asyncio.wait_for(wait_until_started(), timeout=1)
        await asyncio.sleep(0)
        assert not verification.done()
    finally:
        release.set()

    assert await verification is True


def test_verifier_uses_popen_under_selector_event_loop(monkeypatch):
    class FakeStream:
        def __init__(self, value):
            self.value = value
            self.closed = False

        def write(self, value):
            return len(value)

        def read(self, _size):
            value, self.value = self.value, b""
            return value

        def close(self):
            self.closed = True

    class CompletedProcess:
        def __init__(self):
            self.stdin = FakeStream(b"")
            self.stdout = FakeStream(
                json.dumps({"ok": True, "address": "0x1"}).encode("utf-8")
            )
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(
        wallet_verification.subprocess,
        "Popen",
        lambda *_args, **_kwargs: CompletedProcess(),
    )
    monkeypatch.setattr(
        wallet_verification,
        "_VERIFIER_SCRIPT",
        Path("verify.mjs"),
    )
    monkeypatch.setattr(
        wallet_verification.Path,
        "is_file",
        lambda _path: True,
    )
    monkeypatch.setattr(
        wallet_verification.Path,
        "is_dir",
        lambda _path: True,
    )
    monkeypatch.setattr(
        wallet_verification.asyncio,
        "create_subprocess_exec",
        lambda *_args, **_kwargs: pytest.fail(
            "The verifier must not use asyncio subprocess transports."
        ),
    )

    async def verify():
        return await verify_personal_message_signature(
            b"message",
            base64.b64encode(b"signature").decode("ascii"),
            "0x1",
        )

    loop = asyncio.SelectorEventLoop()
    try:
        assert loop.run_until_complete(verify()) is True
    finally:
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


@pytest.mark.asyncio
async def test_verifier_cancellation_waits_for_cleanup_before_releasing_slot(monkeypatch):
    child_started = threading.Event()
    pipe_io_finished = threading.Event()
    cleanup_waiting = threading.Event()
    allow_reap = threading.Event()

    class BlockingPipe:
        def __init__(self):
            self.closed = False

        def write(self, value):
            pipe_io_finished.wait()
            return len(value)

        def read(self, _size):
            pipe_io_finished.wait()
            return b""

        def close(self):
            self.closed = True

    class CancellableProcess:
        def __init__(self):
            self.stdin = BlockingPipe()
            self.stdout = BlockingPipe()
            self.returncode = None
            self.terminating = False
            self.reaped = False

        def wait(self, timeout=None):
            if self.terminating:
                cleanup_waiting.set()
                if not allow_reap.wait(timeout):
                    raise subprocess.TimeoutExpired("node", timeout)
                pipe_io_finished.set()
                self.returncode = -15
                self.reaped = True
                return self.returncode
            threading.Event().wait(timeout)
            raise subprocess.TimeoutExpired("node", timeout)

        def terminate(self):
            self.terminating = True

        def kill(self):
            pipe_io_finished.set()
            self.returncode = -9

        def poll(self):
            return self.returncode

    process = CancellableProcess()

    def create_process(*_args, **_kwargs):
        child_started.set()
        return process

    monkeypatch.setattr(wallet_verification.subprocess, "Popen", create_process)
    monkeypatch.setattr(
        wallet_verification,
        "_VERIFIER_TERMINATE_GRACE_SECONDS",
        0.5,
    )
    monkeypatch.setattr(
        wallet_verification,
        "_VERIFIER_KILL_GRACE_SECONDS",
        0.5,
    )
    verification = asyncio.create_task(
        verify_personal_message_signature(
            b"message",
            base64.b64encode(b"signature").decode("ascii"),
            "0x1",
        )
    )

    async def wait_for_event(event):
        for _ in range(100):
            if event.is_set():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("Expected subprocess lifecycle event did not occur.")

    await wait_for_event(child_started)
    verification.cancel()
    await wait_for_event(cleanup_waiting)
    assert wallet_verification._VERIFIER_CONCURRENCY._value == 3
    allow_reap.set()
    with pytest.raises(asyncio.CancelledError):
        await verification
    assert process.reaped
    assert pipe_io_finished.is_set()
    assert process.stdin.closed
    assert process.stdout.closed
    assert wallet_verification._VERIFIER_CONCURRENCY._value == 4


@pytest.mark.asyncio
async def test_verifier_cleanup_failure_is_sanitized(monkeypatch, caplog):
    class FakePipe:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class UnstoppableProcess:
        def __init__(self):
            self.stdin = FakePipe()
            self.stdout = FakePipe()

        def poll(self):
            return None

        def terminate(self):
            raise OSError("sensitive diagnostic marker")

        def kill(self):
            raise OSError("sensitive diagnostic marker")

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("node", timeout)

    process = UnstoppableProcess()
    semaphore = asyncio.Semaphore(4)
    monkeypatch.setattr(wallet_verification, "_VERIFIER_CONCURRENCY", semaphore)
    monkeypatch.setattr(
        wallet_verification.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(wallet_verification, "_VERIFIER_TIMEOUT_SECONDS", 0)
    with pytest.raises(WalletVerificationUnavailableError) as error:
        await verify_personal_message_signature(
            b"private message",
            base64.b64encode(b"private signature").decode("ascii"),
            "0x1",
            correlation_id="safe-correlation-id",
        )
    assert "sensitive diagnostic marker" not in str(error.value)
    assert "private message" not in caplog.text
    assert "private signature" not in caplog.text
    assert "sensitive diagnostic marker" not in caplog.text
    assert "correlation_id=safe-correlation-id stage=cleanup result=failed" in caplog.text
    assert process.stdin.closed
    assert process.stdout.closed
    assert semaphore._value == 3


@pytest.mark.asyncio
async def test_verifier_concurrency_limit_remains_four(monkeypatch):
    release_workers = threading.Event()
    active_lock = threading.Lock()
    active_workers = 0
    maximum_active_workers = 0
    four_workers_started = threading.Event()

    def blocking_verifier(_request, cancellation_requested=None):
        nonlocal active_workers, maximum_active_workers
        with active_lock:
            active_workers += 1
            maximum_active_workers = max(maximum_active_workers, active_workers)
            if active_workers == 4:
                four_workers_started.set()
        while not release_workers.wait(0.01):
            if cancellation_requested is not None and cancellation_requested.is_set():
                raise RuntimeError("cancelled")
        with active_lock:
            active_workers -= 1
        return json.dumps({"ok": True, "address": "0x1"}).encode("utf-8"), 0

    monkeypatch.setattr(
        wallet_verification,
        "_VERIFIER_CONCURRENCY",
        asyncio.Semaphore(4),
    )
    monkeypatch.setattr(
        wallet_verification,
        "_run_wallet_verifier",
        blocking_verifier,
    )
    tasks = [
        asyncio.create_task(
            verify_personal_message_signature(
                b"message",
                base64.b64encode(b"signature").decode("ascii"),
                "0x1",
            )
        )
        for _ in range(5)
    ]

    async def wait_for_four_workers():
        for _ in range(100):
            if four_workers_started.is_set():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("Four verifier workers did not start.")

    try:
        await wait_for_four_workers()
        assert maximum_active_workers == 4
        assert not tasks[4].done()
    finally:
        release_workers.set()
    assert await asyncio.gather(*tasks) == [True] * 5
    assert maximum_active_workers == 4


@pytest.mark.asyncio
async def test_verifier_semaphore_acquisition_remains_bounded(monkeypatch):
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()
    monkeypatch.setattr(wallet_verification, "_VERIFIER_CONCURRENCY", semaphore)
    monkeypatch.setattr(wallet_verification, "_VERIFIER_WAIT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        wallet_verification,
        "_run_wallet_verifier",
        lambda *_args: pytest.fail("The verifier must not start while the slot is held."),
    )
    try:
        with pytest.raises(WalletVerificationUnavailableError, match="busy"):
            await verify_personal_message_signature(
                b"message",
                base64.b64encode(b"signature").decode("ascii"),
                "0x1",
            )
    finally:
        semaphore.release()


@pytest.mark.asyncio
async def test_concurrency_test_mock_forwards_correlation_id(monkeypatch):
    received_ids = []

    async def fake_verifier(
        _message,
        _signature,
        _address,
        *,
        network="mainnet",
        correlation_id=None,
    ):
        assert network == "mainnet"
        received_ids.append(correlation_id)
        return True

    monkeypatch.setattr(
        wallet_verification,
        "verify_personal_message_signature",
        fake_verifier,
    )
    _hold_signatures_until_concurrent(monkeypatch, count=2)
    verify = wallet_verification.verify_personal_message_signature
    await asyncio.gather(
        verify(b"message", "signature", "0x1", correlation_id="correlation-one"),
        verify(b"message", "signature", "0x1", correlation_id="correlation-two"),
    )
    assert sorted(received_ids) == ["correlation-one", "correlation-two"]


@pytest.mark.asyncio
async def test_verifier_rejects_oversized_output(monkeypatch):
    class FakeStdin:
        def write(self, _value):
            return len(_value)

        def close(self):
            return None

    class OversizedStream:
        def __init__(self):
            self.remaining = 513

        def read(self, size):
            chunk_size = min(size, self.remaining)
            self.remaining -= chunk_size
            return b"x" * chunk_size

        def close(self):
            return None

    class OversizedProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = OversizedStream()
            self.returncode = None
            self.killed = False
            self.terminated = False
            self.reaped = False

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None):
            self.reaped = True
            return self.returncode

        def poll(self):
            return self.returncode

    process = OversizedProcess()

    def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(wallet_verification.subprocess, "Popen", create_process)
    with pytest.raises(WalletVerificationUnavailableError, match="invalid response"):
        await verify_personal_message_signature(
            b"message",
            base64.b64encode(b"signature").decode("ascii"),
            "0x1",
        )
    assert process.terminated
    assert process.reaped
