"""Server-side Sui wallet verification helpers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from asyncio.subprocess import PIPE

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.domain import (
    AnonymousVisitorIdentity,
    WalletAssociation,
    WalletVerificationChallenge,
    generate_uuid,
)

SUPPORTED_SUI_NETWORKS = {"mainnet", "testnet", "devnet"}
SUPPORTED_VERIFICATION_PURPOSES = {"wallet_link"}
_WALLET_CHAIN = "sui"
_VERIFIER_ROOT = Path(__file__).resolve().parents[2] / "wallet-verifier"
_VERIFIER_SCRIPT = _VERIFIER_ROOT / "verify.mjs"
_MAX_SIGNATURE_LENGTH = 16_384
_MAX_VERIFIER_OUTPUT_LENGTH = 512
_VERIFIER_TIMEOUT_SECONDS = 5
_VERIFIER_WAIT_TIMEOUT_SECONDS = 2
_VERIFIER_CONCURRENCY = asyncio.Semaphore(4)


async def _read_bounded_verifier_output(stream: asyncio.StreamReader) -> bytes:
    output = bytearray()
    while True:
        chunk = await stream.read(
            min(256, _MAX_VERIFIER_OUTPUT_LENGTH + 1 - len(output))
        )
        if not chunk:
            return bytes(output)
        output.extend(chunk)
        if len(output) > _MAX_VERIFIER_OUTPUT_LENGTH:
            raise WalletVerificationUnavailableError(
                "The Sui signature verification service returned an invalid response."
            )


class WalletVerificationError(RuntimeError):
    """Base error for wallet verification failures."""


class WalletVerificationUnavailableError(WalletVerificationError):
    """The official server-side verification path is unavailable."""


class InvalidWalletChallengeError(ValueError):
    """The challenge is malformed, expired, or not valid for the current context."""


class WalletAlreadyLinkedError(WalletVerificationError):
    """The wallet is already linked to a different visitor identity."""


class WalletAssociationError(WalletVerificationError):
    """A stored wallet association is invalid for the configured Sui network."""


def normalize_sui_address(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidWalletChallengeError("Wallet address is required.")
    candidate = value.strip()
    if not candidate.startswith("0x"):
        raise InvalidWalletChallengeError("Wallet address must start with 0x.")
    body = candidate[2:]
    if len(body) < 1 or len(body) > 64:
        raise InvalidWalletChallengeError("Wallet address is invalid.")
    if any(character not in "0123456789abcdefABCDEF" for character in body):
        raise InvalidWalletChallengeError("Wallet address is invalid.")
    return "0x" + body.lower().zfill(64)


async def resolve_verified_visitor_wallet(
    session: AsyncSession,
    *,
    user_id: str,
    visitor_identity_id: str,
) -> WalletAssociation | None:
    """Resolve only the active verified wallet owned by this exact visitor and user."""
    if not user_id or not visitor_identity_id:
        raise ValueError("Authenticated user and visitor identity are required.")

    association = await session.scalar(
        select(WalletAssociation)
        .join(
            AnonymousVisitorIdentity,
            AnonymousVisitorIdentity.id == WalletAssociation.visitor_identity_id,
        )
        .where(
            WalletAssociation.user_id == user_id,
            WalletAssociation.visitor_identity_id == visitor_identity_id,
            AnonymousVisitorIdentity.user_id == user_id,
            AnonymousVisitorIdentity.id == visitor_identity_id,
            WalletAssociation.revoked_at.is_(None),
        )
    )
    if association is None:
        return None

    configured_network = settings.SUI_NETWORK
    if not isinstance(configured_network, str):
        raise WalletAssociationError("The configured Sui network is invalid.")
    configured_network = configured_network.strip().casefold()
    if configured_network not in SUPPORTED_SUI_NETWORKS:
        raise WalletAssociationError("The configured Sui network is invalid.")
    if (
        association.chain != _WALLET_CHAIN
        or not isinstance(association.network, str)
        or association.network.casefold() != configured_network
    ):
        raise WalletAssociationError(
            "The verified wallet association does not match the configured Sui network."
        )
    try:
        if normalize_sui_address(association.canonical_address) != association.canonical_address:
            raise WalletAssociationError("The verified wallet address is not canonical.")
    except InvalidWalletChallengeError as exc:
        raise WalletAssociationError("The verified wallet address is invalid.") from exc

    verified_at = association.verified_at
    if not isinstance(verified_at, datetime):
        raise WalletAssociationError("The verified wallet association has an invalid timestamp.")
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    try:
        is_future = verified_at > _utc_now()
    except (TypeError, ValueError, OverflowError) as exc:
        raise WalletAssociationError(
            "The verified wallet association has an invalid timestamp."
        ) from exc
    if is_future:
        raise WalletAssociationError("The verified wallet association has an invalid timestamp.")
    return association


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_nonce() -> str:
    return secrets.token_hex(16)


def _normalized_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _message_timestamp(value: datetime) -> str:
    return _normalized_datetime(value).isoformat().replace("+00:00", "Z")


def normalize_wallet_origin(value: str | None) -> str:
    """Return an exact configured application origin or reject the request."""
    if not isinstance(value, str) or not value:
        raise InvalidWalletChallengeError("A valid application origin is required.")

    def canonical_origin(candidate: str) -> str | None:
        try:
            parsed = urlsplit(candidate)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                return None
            hostname = parsed.hostname.casefold()
            if ":" in hostname:
                hostname = f"[{hostname}]"
            port = parsed.port
            if port and not (
                (parsed.scheme == "https" and port == 443)
                or (parsed.scheme == "http" and port == 80)
            ):
                hostname = f"{hostname}:{port}"
            return f"{parsed.scheme.casefold()}://{hostname}"
        except ValueError:
            return None

    origin = canonical_origin(value)
    configured_origins = {
        configured
        for item in settings.cors_origins_list
        if (configured := canonical_origin(item)) is not None
    }
    if origin is None or origin not in configured_origins:
        raise InvalidWalletChallengeError("The application origin is not allowed.")
    return origin


def _message_payload(
    *,
    purpose: str,
    network: str,
    address: str,
    nonce: str,
    visitor_identity_id: str,
    user_id: str,
    created_at: datetime,
    expires_at: datetime,
    origin: str,
) -> dict[str, Any]:
    return {
        "address": address,
        "chain": _WALLET_CHAIN,
        "created_at": _message_timestamp(created_at),
        "domain": urlsplit(origin).netloc,
        "expires_at": _message_timestamp(expires_at),
        "network": network,
        "nonce": nonce,
        "origin": origin,
        "purpose": purpose,
        "user_id": user_id,
        "visitor_identity_id": visitor_identity_id,
    }


def build_wallet_verification_message(
    *,
    purpose: str,
    network: str,
    address: str,
    nonce: str,
    visitor_identity_id: str,
    user_id: str,
    created_at: datetime,
    expires_at: datetime,
    origin: str,
) -> bytes:
    payload = _message_payload(
        purpose=purpose,
        network=network,
        address=address,
        nonce=nonce,
        visitor_identity_id=visitor_identity_id,
        user_id=user_id,
        created_at=created_at,
        expires_at=expires_at,
        origin=origin,
    )
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


async def create_wallet_verification_challenge(
    session: AsyncSession,
    *,
    visitor_identity_id: str,
    user_id: str,
    purpose: str,
    network: str,
    address: str,
    origin: str,
    ttl_seconds: int = 300,
) -> tuple[WalletVerificationChallenge, bytes]:
    if purpose not in SUPPORTED_VERIFICATION_PURPOSES:
        raise InvalidWalletChallengeError("Wallet verification purpose is not supported.")
    if network not in SUPPORTED_SUI_NETWORKS:
        raise InvalidWalletChallengeError("Sui network is not supported.")
    origin = normalize_wallet_origin(origin)
    configured_network = settings.SUI_NETWORK.strip().casefold()
    if network != configured_network:
        raise InvalidWalletChallengeError("Sui network does not match the configured network.")
    normalized_address = normalize_sui_address(address)
    created_at = _utc_now()
    expires_at = created_at + timedelta(seconds=ttl_seconds)
    nonce = _new_nonce()
    message = build_wallet_verification_message(
        purpose=purpose,
        network=network,
        address=normalized_address,
        nonce=nonce,
        visitor_identity_id=visitor_identity_id,
        user_id=user_id,
        created_at=created_at,
        expires_at=expires_at,
        origin=origin,
    )
    challenge = WalletVerificationChallenge(
        id=generate_uuid(),
        visitor_identity_id=visitor_identity_id,
        user_id=user_id,
        purpose=purpose,
        chain=_WALLET_CHAIN,
        network=network,
        canonical_address=normalized_address,
        nonce=nonce,
        nonce_hash=hashlib.sha256(nonce.encode("ascii")).hexdigest(),
        message_digest=hashlib.sha256(message).hexdigest(),
        created_at=created_at,
        expires_at=expires_at,
    )
    session.add(challenge)
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise WalletVerificationError("Could not create the wallet challenge.") from exc
    return challenge, message


async def fetch_wallet_verification_challenge(
    session: AsyncSession,
    *,
    challenge_id: str,
    visitor_identity_id: str,
) -> WalletVerificationChallenge | None:
    result = await session.execute(
        select(WalletVerificationChallenge).where(
            WalletVerificationChallenge.id == challenge_id,
            WalletVerificationChallenge.visitor_identity_id == visitor_identity_id,
        )
    )
    return result.scalar_one_or_none()


async def verify_personal_message_signature(message: bytes, signature: str, address: str) -> bool:
    if not isinstance(signature, str) or not signature:
        return False
    if len(signature) > _MAX_SIGNATURE_LENGTH:
        return False
    if not isinstance(address, str) or not address.startswith("0x"):
        return False
    if not _VERIFIER_SCRIPT.is_file() or not (_VERIFIER_ROOT / "node_modules").is_dir():
        raise WalletVerificationUnavailableError(
            "The Sui signature verification dependency is not installed."
        )
    try:
        await asyncio.wait_for(
            _VERIFIER_CONCURRENCY.acquire(),
            timeout=_VERIFIER_WAIT_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise WalletVerificationUnavailableError(
            "The Sui signature verification service is busy."
        ) from exc

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            "node",
            str(_VERIFIER_SCRIPT),
            cwd=str(_VERIFIER_ROOT),
            stdin=PIPE,
            stdout=PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=_MAX_VERIFIER_OUTPUT_LENGTH + 1,
        )
        request = json.dumps(
            {
                "message": base64.b64encode(message).decode("ascii"),
                "signature": signature,
                "address": address,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        process.stdin.write(request)
        await process.stdin.drain()
        process.stdin.close()
        output_task = asyncio.create_task(
            _read_bounded_verifier_output(process.stdout)
        )
        try:
            stdout, return_code = await asyncio.wait_for(
                asyncio.gather(output_task, process.wait()),
                timeout=_VERIFIER_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise WalletVerificationUnavailableError(
                "The Sui signature verification service timed out."
            ) from exc
        if return_code != 0:
            raise WalletVerificationUnavailableError(
                "The Sui signature verification service failed."
            )
        payload = json.loads(stdout.decode("utf-8"))
    except WalletVerificationUnavailableError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WalletVerificationUnavailableError(
            "The Sui signature verification service is unavailable."
        ) from exc
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        _VERIFIER_CONCURRENCY.release()

    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        raise WalletVerificationUnavailableError(
            "The Sui signature verification service returned an invalid response."
        )
    if not payload.get("ok"):
        return False
    verified_address = payload.get("address")
    if not isinstance(verified_address, str):
        return False
    return verified_address.lower() == address.lower()


def _challenge_message(
    challenge: WalletVerificationChallenge,
    *,
    origin: str,
) -> bytes:
    return build_wallet_verification_message(
        purpose=challenge.purpose,
        network=challenge.network,
        address=challenge.canonical_address,
        nonce=challenge.nonce,
        visitor_identity_id=challenge.visitor_identity_id,
        user_id=challenge.user_id,
        created_at=_normalized_datetime(challenge.created_at),
        expires_at=_normalized_datetime(challenge.expires_at),
        origin=normalize_wallet_origin(origin),
    )


async def consume_wallet_challenge(
    session: AsyncSession,
    *,
    challenge_id: str,
    visitor_identity_id: str,
    user_id: str,
    signature: str,
    origin: str,
) -> WalletAssociation:
    now = _utc_now()
    challenge = await fetch_wallet_verification_challenge(
        session,
        challenge_id=challenge_id,
        visitor_identity_id=visitor_identity_id,
    )
    if challenge is None or challenge.user_id != user_id:
        raise InvalidWalletChallengeError("Wallet verification challenge is invalid.")
    if challenge.revoked_at is not None:
        raise InvalidWalletChallengeError("Wallet verification challenge is invalid.")
    if challenge.consumed_at is not None:
        raise InvalidWalletChallengeError("Wallet verification challenge has already been used.")
    if challenge.expires_at.tzinfo is None:
        challenge.expires_at = challenge.expires_at.replace(tzinfo=timezone.utc)
    if challenge.expires_at <= now:
        raise InvalidWalletChallengeError("Wallet verification challenge has expired.")

    message = _challenge_message(challenge, origin=origin)
    try:
        nonce_digest = hashlib.sha256(challenge.nonce.encode("ascii")).hexdigest()
    except UnicodeEncodeError as exc:
        raise InvalidWalletChallengeError(
            "Wallet verification challenge is invalid."
        ) from exc
    if not hmac.compare_digest(
        hashlib.sha256(message).hexdigest(),
        challenge.message_digest,
    ) or not hmac.compare_digest(nonce_digest, challenge.nonce_hash):
        raise InvalidWalletChallengeError("Wallet verification challenge is invalid.")
    if (
        challenge.chain != _WALLET_CHAIN
        or challenge.purpose not in SUPPORTED_VERIFICATION_PURPOSES
        or challenge.network not in SUPPORTED_SUI_NETWORKS
    ):
        raise InvalidWalletChallengeError("Wallet verification challenge is invalid.")

    await session.commit()
    if not await verify_personal_message_signature(
        message,
        signature,
        challenge.canonical_address,
    ):
        raise InvalidWalletChallengeError("Wallet verification signature is invalid.")

    now = _utc_now()
    consumed = await session.execute(
        update(WalletVerificationChallenge)
        .where(
            WalletVerificationChallenge.id == challenge_id,
            WalletVerificationChallenge.visitor_identity_id == visitor_identity_id,
            WalletVerificationChallenge.consumed_at.is_(None),
            WalletVerificationChallenge.revoked_at.is_(None),
            WalletVerificationChallenge.expires_at > now,
        )
        .values(consumed_at=now)
    )
    if consumed.rowcount != 1:
        await session.rollback()
        raise InvalidWalletChallengeError("Wallet verification challenge is invalid.")

    existing = await session.execute(
        select(WalletAssociation).where(
            WalletAssociation.chain == _WALLET_CHAIN,
            WalletAssociation.network == challenge.network,
            WalletAssociation.canonical_address == challenge.canonical_address,
            WalletAssociation.revoked_at.is_(None),
        )
    )
    linked = existing.scalar_one_or_none()
    if linked is not None and (
        linked.visitor_identity_id != visitor_identity_id
        or linked.user_id != user_id
    ):
        await session.rollback()
        raise WalletAlreadyLinkedError("This wallet is already linked to another visitor identity.")

    visitor_link = await session.execute(
        select(WalletAssociation).where(
            WalletAssociation.visitor_identity_id == visitor_identity_id,
            WalletAssociation.revoked_at.is_(None),
        )
    )
    current = visitor_link.scalar_one_or_none()
    if current is not None and (
        current.user_id != user_id
        or current.canonical_address != challenge.canonical_address
    ):
        await session.rollback()
        raise WalletAlreadyLinkedError("This visitor identity already has a verified wallet.")

    if linked is None:
        association = WalletAssociation(
            id=generate_uuid(),
            visitor_identity_id=visitor_identity_id,
            user_id=user_id,
            chain=_WALLET_CHAIN,
            network=challenge.network,
            canonical_address=challenge.canonical_address,
            verified_at=now,
            created_at=now,
        )
        session.add(association)
    else:
        association = linked
        if association.visitor_identity_id != visitor_identity_id:
            raise WalletAlreadyLinkedError("This wallet is already linked to another visitor identity.")
        association.verified_at = now
        association.created_at = association.created_at or now

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise WalletAlreadyLinkedError("This wallet is already linked.") from exc
    except SQLAlchemyError as exc:
        await session.rollback()
        raise WalletVerificationError("Could not verify the wallet challenge.") from exc
    return association
