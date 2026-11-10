"""Server-side Sui wallet verification helpers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import asyncio
import logging
import subprocess
import time
from threading import Event, Thread
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
SUPPORTED_WALLET_VERIFICATION_NETWORKS = {"mainnet", "testnet"}
SUPPORTED_VERIFICATION_PURPOSES = {"wallet_link"}
_WALLET_CHAIN = "sui"
logger = logging.getLogger("orhex.api.wallet_verification")
_VERIFIER_ROOT = Path(__file__).resolve().parents[2] / "wallet-verifier"
_VERIFIER_SCRIPT = _VERIFIER_ROOT / "verify.mjs"
_MAX_SIGNATURE_LENGTH = 16_384
_MAX_VERIFIER_OUTPUT_LENGTH = 512
_VERIFIER_TIMEOUT_SECONDS = 5
_VERIFIER_WAIT_TIMEOUT_SECONDS = 2
_VERIFIER_POLL_INTERVAL_SECONDS = 0.05
_VERIFIER_TERMINATE_GRACE_SECONDS = 0.25
_VERIFIER_KILL_GRACE_SECONDS = 1
_VERIFIER_PIPE_JOIN_GRACE_SECONDS = 0.25
_VERIFIER_CONCURRENCY = asyncio.Semaphore(4)


class _VerifierCleanupError(RuntimeError):
    pass


class _VerifierCancellationRequested(RuntimeError):
    pass


def _close_verifier_pipe(pipe, errors: list[Exception]) -> None:
    if pipe is None:
        return
    try:
        pipe.close()
    except Exception as exc:
        errors.append(exc)


def _cleanup_wallet_verifier(
    process: subprocess.Popen,
    threads: tuple[Thread, ...],
    unopened_pipes: tuple[object, ...],
) -> None:
    cleanup_failed = False
    try:
        is_running = process.poll() is None
    except OSError:
        is_running = True
        cleanup_failed = True

    if is_running:
        try:
            process.terminate()
        except OSError:
            cleanup_failed = True

    return_code = None
    try:
        return_code = process.wait(timeout=_VERIFIER_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    except OSError:
        cleanup_failed = True

    if return_code is None:
        try:
            process.kill()
        except OSError:
            cleanup_failed = True
        try:
            return_code = process.wait(timeout=_VERIFIER_KILL_GRACE_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            cleanup_failed = True

    for pipe in unopened_pipes:
        try:
            pipe.close()
        except Exception:
            cleanup_failed = True

    for thread in threads:
        thread.join(timeout=_VERIFIER_PIPE_JOIN_GRACE_SECONDS)
        if thread.is_alive():
            cleanup_failed = True

    if return_code is None:
        cleanup_failed = True
    if cleanup_failed:
        raise _VerifierCleanupError(
            "The Sui verifier process could not be stopped and reaped safely."
        ) from None


def _run_wallet_verifier(
    request: bytes,
    cancellation_requested: Event | None = None,
) -> tuple[bytes, int]:
    deadline = time.monotonic() + _VERIFIER_TIMEOUT_SECONDS
    process: subprocess.Popen | None = None
    output = bytearray()
    output_exceeded = Event()
    reader_done = Event()
    writer_done = Event()
    reader_errors: list[Exception] = []
    writer_errors: list[Exception] = []
    reader: Thread | None = None
    writer: Thread | None = None
    reader_started = False
    writer_started = False

    def read_output() -> None:
        try:
            if process is None:
                raise OSError("Could not read the Sui verifier process output.")
            if process.stdout is None:
                raise OSError("Could not read the Sui verifier process output.")
            while True:
                chunk = process.stdout.read(
                    min(256, _MAX_VERIFIER_OUTPUT_LENGTH + 1 - len(output))
                )
                if not chunk:
                    return
                output.extend(chunk)
                if len(output) > _MAX_VERIFIER_OUTPUT_LENGTH:
                    output_exceeded.set()
                    return
        except Exception as exc:
            reader_errors.append(exc)
        finally:
            if process is not None:
                _close_verifier_pipe(process.stdout, reader_errors)
            reader_done.set()

    def write_input() -> None:
        try:
            if process is None:
                raise OSError("Could not write to the Sui verifier process.")
            if process.stdin is None:
                raise OSError("Could not write to the Sui verifier process.")
            process.stdin.write(request)
        except Exception as exc:
            writer_errors.append(exc)
        finally:
            if process is not None:
                _close_verifier_pipe(process.stdin, writer_errors)
            writer_done.set()

    try:
        # Popen itself is synchronous and cannot be interrupted while the OS creates the process.
        process = subprocess.Popen(
            ["node", str(_VERIFIER_SCRIPT)],
            cwd=str(_VERIFIER_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if cancellation_requested is not None and cancellation_requested.is_set():
            raise _VerifierCancellationRequested()
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired("node", _VERIFIER_TIMEOUT_SECONDS)
        if process.stdin is None or process.stdout is None:
            raise OSError("Could not open the Sui verifier process pipes.")
        reader = Thread(target=read_output, name="sui-verifier-stdout", daemon=True)
        writer = Thread(target=write_input, name="sui-verifier-stdin", daemon=True)
        reader.start()
        reader_started = True
        writer.start()
        writer_started = True
        while True:
            if cancellation_requested is not None and cancellation_requested.is_set():
                raise _VerifierCancellationRequested()
            if output_exceeded.is_set():
                raise WalletVerificationUnavailableError(
                    "The Sui signature verification service returned an invalid response."
                )
            if reader_done.is_set() and reader_errors:
                raise reader_errors[0]
            if writer_done.is_set() and writer_errors:
                raise writer_errors[0]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("node", _VERIFIER_TIMEOUT_SECONDS)
            try:
                return_code = process.wait(
                    timeout=min(remaining, _VERIFIER_POLL_INTERVAL_SECONDS)
                )
                break
            except subprocess.TimeoutExpired:
                continue

        for thread in (reader, writer):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("node", _VERIFIER_TIMEOUT_SECONDS)
            thread.join(timeout=remaining)
            if thread.is_alive():
                raise subprocess.TimeoutExpired("node", _VERIFIER_TIMEOUT_SECONDS)
        if reader_errors:
            raise reader_errors[0]
        if writer_errors:
            raise writer_errors[0]
        if output_exceeded.is_set():
            raise WalletVerificationUnavailableError(
                "The Sui signature verification service returned an invalid response."
            )
        return bytes(output), return_code
    except BaseException:
        if process is not None:
            started_threads = tuple(
                thread
                for thread, started in (
                    (reader, reader_started),
                    (writer, writer_started),
                )
                if thread is not None and started
            )
            unopened_pipes = tuple(
                pipe
                for pipe, started in (
                    (process.stdout, reader_started),
                    (process.stdin, writer_started),
                )
                if pipe is not None and not started
            )
            _cleanup_wallet_verifier(process, started_threads, unopened_pipes)
        raise


class WalletVerificationError(RuntimeError):
    """Base error for wallet verification failures."""

    code = "wallet_verification_internal_error"


class WalletVerificationUnavailableError(WalletVerificationError):
    """The official server-side verification path is unavailable."""

    code = "wallet_verification_unavailable"


class InvalidWalletChallengeError(ValueError):
    """The challenge is malformed, expired, or not valid for the current context."""

    code = "wallet_challenge_invalid"


class WalletChallengeNotFoundError(InvalidWalletChallengeError):
    code = "wallet_challenge_not_found"


class WalletChallengeExpiredError(InvalidWalletChallengeError):
    code = "wallet_challenge_expired"


class WalletChallengeConsumedError(InvalidWalletChallengeError):
    code = "wallet_challenge_consumed"


class WalletSignedMessageMismatchError(InvalidWalletChallengeError):
    code = "wallet_signed_message_mismatch"


class WalletAddressMismatchError(InvalidWalletChallengeError):
    code = "wallet_address_mismatch"


class WalletDerivedAddressMismatchError(WalletAddressMismatchError):
    code = "wallet_derived_address_mismatch"


class WalletSignatureMissingError(InvalidWalletChallengeError):
    code = "wallet_signature_missing"


class InvalidWalletSignatureEncodingError(InvalidWalletChallengeError):
    code = "wallet_signature_encoding_invalid"


class InvalidWalletSignatureError(InvalidWalletChallengeError):
    code = "wallet_signature_verification_failed"


class UnsupportedWalletSignatureSchemeError(InvalidWalletChallengeError):
    code = "wallet_signature_scheme_unsupported"


class WalletAlreadyLinkedError(WalletVerificationError):
    """The wallet is already linked to a different visitor identity."""

    code = "wallet_already_linked"


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


async def revoke_verified_visitor_wallet(
    session: AsyncSession,
    *,
    user_id: str,
    visitor_identity_id: str,
) -> None:
    now = _utc_now()
    try:
        await session.execute(
            update(WalletAssociation)
            .where(
                WalletAssociation.user_id == user_id,
                WalletAssociation.visitor_identity_id == visitor_identity_id,
                WalletAssociation.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await session.execute(
            update(WalletVerificationChallenge)
            .where(
                WalletVerificationChallenge.user_id == user_id,
                WalletVerificationChallenge.visitor_identity_id == visitor_identity_id,
                WalletVerificationChallenge.consumed_at.is_(None),
                WalletVerificationChallenge.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise WalletVerificationError("Could not revoke the verified wallet link.") from exc


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
    ttl_seconds: int = 600,
) -> tuple[WalletVerificationChallenge, bytes]:
    if purpose not in SUPPORTED_VERIFICATION_PURPOSES:
        raise InvalidWalletChallengeError("Wallet verification purpose is not supported.")
    if network not in SUPPORTED_WALLET_VERIFICATION_NETWORKS:
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
        raise WalletVerificationUnavailableError(
            "Could not create the wallet challenge."
        ) from exc
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


def _decode_wallet_signature(signature: str) -> bytes:
    if signature is None or signature == "":
        raise WalletSignatureMissingError("Wallet signature is required.")
    if not isinstance(signature, str) or len(signature) > _MAX_SIGNATURE_LENGTH:
        raise InvalidWalletSignatureEncodingError("Wallet signature encoding is invalid.")
    try:
        decoded = base64.b64decode(signature, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise InvalidWalletSignatureEncodingError(
            "Wallet signature encoding is invalid."
        ) from exc
    if not decoded or base64.b64encode(decoded).decode("ascii") != signature:
        raise InvalidWalletSignatureEncodingError("Wallet signature encoding is invalid.")
    return decoded


async def verify_personal_message_signature(
    message: bytes,
    signature: str,
    address: str,
    *,
    network: str = "mainnet",
    correlation_id: str | None = None,
) -> bool:
    _decode_wallet_signature(signature)
    if not isinstance(address, str) or not address.startswith("0x"):
        raise InvalidWalletChallengeError("Wallet challenge address is invalid.")
    if (
        not isinstance(network, str)
        or network not in SUPPORTED_WALLET_VERIFICATION_NETWORKS
    ):
        raise InvalidWalletChallengeError("Sui network is not supported.")
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

    release_verifier_slot = True
    try:
        cancellation_requested = Event()
        request = json.dumps(
            {
                "message": base64.b64encode(message).decode("ascii"),
                "signature": signature,
                "address": address,
                "network": network,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        logger.info(
            "Wallet verification diagnostic: correlation_id=%s stage=verifier_input message_present=%s signature_present=%s address_present=%s",
            correlation_id or "unavailable",
            bool(message),
            bool(signature),
            bool(address),
        )
        verifier_task = asyncio.create_task(
            asyncio.to_thread(
                _run_wallet_verifier,
                request,
                cancellation_requested,
            )
        )
        try:
            stdout, return_code = await asyncio.shield(verifier_task)
        except asyncio.CancelledError:
            cancellation_requested.set()
            while not verifier_task.done():
                try:
                    await asyncio.shield(verifier_task)
                except asyncio.CancelledError:
                    cancellation_requested.set()
                except Exception:
                    break
            if verifier_task.done() and not verifier_task.cancelled():
                worker_error = verifier_task.exception()
                if isinstance(worker_error, _VerifierCleanupError):
                    release_verifier_slot = False
                    logger.error(
                        "Wallet verification diagnostic: correlation_id=%s stage=cleanup result=failed",
                        correlation_id or "unavailable",
                    )
            raise
        if return_code != 0:
            raise WalletVerificationUnavailableError(
                "The Sui signature verification service failed."
            )
        payload = json.loads(stdout.decode("utf-8"))
    except WalletVerificationUnavailableError:
        raise
    except _VerifierCleanupError:
        release_verifier_slot = False
        logger.error(
            "Wallet verification diagnostic: correlation_id=%s stage=cleanup result=failed",
            correlation_id or "unavailable",
        )
        raise WalletVerificationUnavailableError(
            "The Sui signature verification service could not be stopped safely."
        ) from None
    except subprocess.TimeoutExpired as exc:
        raise WalletVerificationUnavailableError(
            "The Sui signature verification service timed out."
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WalletVerificationUnavailableError(
            "The Sui signature verification service is unavailable."
        ) from exc
    finally:
        if release_verifier_slot:
            _VERIFIER_CONCURRENCY.release()

    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        raise WalletVerificationUnavailableError(
            "The Sui signature verification service returned an invalid response."
        )
    if not payload.get("ok"):
        verifier_code = payload.get("code")
        if verifier_code == "signature_missing":
            logger.info(
                "Wallet verification diagnostic: correlation_id=%s stage=verifier_result code=%s",
                correlation_id or "unavailable",
                verifier_code,
            )
            raise WalletSignatureMissingError("Wallet signature is required.")
        if verifier_code == "signature_encoding_invalid":
            logger.info(
                "Wallet verification diagnostic: correlation_id=%s stage=verifier_result code=%s scheme=%s",
                correlation_id or "unavailable",
                verifier_code,
                payload.get("scheme", "unknown"),
            )
            raise InvalidWalletSignatureEncodingError("Wallet signature encoding is invalid.")
        if verifier_code == "signature_scheme_unsupported":
            logger.info(
                "Wallet verification diagnostic: correlation_id=%s stage=verifier_result code=%s",
                correlation_id or "unavailable",
                verifier_code,
            )
            raise UnsupportedWalletSignatureSchemeError(
                "The wallet signature scheme is not supported."
            )
        if verifier_code == "derived_address_mismatch":
            logger.info(
                "Wallet verification diagnostic: correlation_id=%s stage=verifier_result code=%s scheme=%s",
                correlation_id or "unavailable",
                verifier_code,
                payload.get("scheme", "unknown"),
            )
            raise WalletDerivedAddressMismatchError(
                "The signature was made by a different Sui address."
            )
        if verifier_code == "signature_verification_failed":
            logger.info(
                "Wallet verification diagnostic: correlation_id=%s stage=verifier_result code=%s scheme=%s",
                correlation_id or "unavailable",
                verifier_code,
                payload.get("scheme", "unknown"),
            )
            raise InvalidWalletSignatureError("Wallet signature verification failed.")
        if verifier_code == "verification_service_error":
            logger.error(
                "Wallet verification diagnostic: correlation_id=%s stage=verification_service code=wallet_verification_unavailable scheme=%s",
                correlation_id or "unavailable",
                payload.get("scheme")
                if isinstance(payload.get("scheme"), str)
                else "unknown",
            )
            raise WalletVerificationUnavailableError(
                "The Sui signature verification service failed."
            )
        logger.error(
            "Sui signature verifier returned an unexpected diagnostic: code=%s",
            verifier_code if isinstance(verifier_code, str) else "missing",
        )
        raise WalletVerificationUnavailableError(
            "The Sui signature verification service returned an invalid response."
        )
    verified_address = payload.get("address")
    if not isinstance(verified_address, str):
        raise WalletVerificationUnavailableError(
            "The Sui signature verification service returned an invalid response."
        )
    if normalize_sui_address(verified_address) != normalize_sui_address(address):
        logger.info(
            "Wallet verification diagnostic: correlation_id=%s stage=address_match result=failed",
            correlation_id or "unavailable",
        )
        raise WalletAddressMismatchError("The signature was made by a different Sui address.")
    logger.info(
        "Wallet verification diagnostic: correlation_id=%s stage=verifier_result parsed=%s signature_verified=%s address_matches=%s scheme=%s",
        correlation_id or "unavailable",
        True,
        True,
        True,
        payload.get("scheme", "unknown"),
    )
    return True


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
    signed_message: str | None,
    origin: str,
    correlation_id: str | None = None,
) -> WalletAssociation:
    now = _utc_now()
    challenge = await fetch_wallet_verification_challenge(
        session,
        challenge_id=challenge_id,
        visitor_identity_id=visitor_identity_id,
    )
    if challenge is None or challenge.user_id != user_id:
        raise WalletChallengeNotFoundError("Wallet verification challenge was not found.")
    if challenge.revoked_at is not None:
        raise WalletChallengeNotFoundError("Wallet verification challenge was not found.")
    if challenge.consumed_at is not None:
        raise WalletChallengeConsumedError("Wallet verification challenge was already used.")
    if challenge.expires_at.tzinfo is None:
        challenge.expires_at = challenge.expires_at.replace(tzinfo=timezone.utc)
    if challenge.expires_at <= now:
        raise WalletChallengeExpiredError("Wallet verification challenge has expired.")
    logger.info(
        "Wallet verification diagnostic: correlation_id=%s stage=challenge_context result=passed expired=%s replayed=%s address_bound=%s",
        correlation_id or "unavailable",
        False,
        False,
        True,
    )

    _decode_wallet_signature(signature)
    message = _challenge_message(challenge, origin=origin)
    if signed_message is not None:
        try:
            decoded_message = base64.b64decode(signed_message, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise WalletSignedMessageMismatchError(
                "The wallet signed message did not match the issued challenge."
            ) from exc
        if base64.b64encode(decoded_message).decode("ascii") != signed_message:
            raise WalletSignedMessageMismatchError(
                "The wallet signed message did not match the issued challenge."
            )
        if not hmac.compare_digest(decoded_message, message):
            logger.info(
                "Wallet verification diagnostic: correlation_id=%s stage=signed_message_match result=failed",
                correlation_id or "unavailable",
            )
            raise WalletSignedMessageMismatchError(
                "The wallet signed message did not match the issued challenge."
            )
        logger.info(
            "Wallet verification diagnostic: correlation_id=%s stage=signed_message_match result=passed",
            correlation_id or "unavailable",
        )
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
        or challenge.network not in SUPPORTED_WALLET_VERIFICATION_NETWORKS
    ):
        raise InvalidWalletChallengeError("Wallet verification challenge is invalid.")

    challenge_network = challenge.network
    challenge_address = challenge.canonical_address
    await session.commit()
    if not await verify_personal_message_signature(
        message,
        signature,
        challenge_address,
        network=challenge_network,
        correlation_id=correlation_id,
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
        latest = await fetch_wallet_verification_challenge(
            session,
            challenge_id=challenge_id,
            visitor_identity_id=visitor_identity_id,
        )
        if latest is None:
            raise WalletChallengeNotFoundError("Wallet verification challenge was not found.")
        if latest.consumed_at is not None:
            raise WalletChallengeConsumedError("Wallet verification challenge was already used.")
        latest_expiry = _normalized_datetime(latest.expires_at)
        if latest_expiry <= _utc_now():
            raise WalletChallengeExpiredError("Wallet verification challenge has expired.")
        raise InvalidWalletChallengeError("Wallet verification challenge is invalid.")

    existing = await session.execute(
        select(WalletAssociation).where(
            WalletAssociation.chain == _WALLET_CHAIN,
            WalletAssociation.network == challenge_network,
            WalletAssociation.canonical_address == challenge_address,
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
        or current.canonical_address != challenge_address
    ):
        await session.rollback()
        raise WalletAlreadyLinkedError("This visitor identity already has a verified wallet.")

    if linked is None:
        association = WalletAssociation(
            id=generate_uuid(),
            visitor_identity_id=visitor_identity_id,
            user_id=user_id,
            chain=_WALLET_CHAIN,
            network=challenge_network,
            canonical_address=challenge_address,
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
        try:
            existing_link = await session.scalar(
                select(WalletAssociation).where(
                    WalletAssociation.chain == _WALLET_CHAIN,
                    WalletAssociation.network == challenge_network,
                    WalletAssociation.canonical_address == challenge_address,
                    WalletAssociation.revoked_at.is_(None),
                )
            )
            visitor_link = await session.scalar(
                select(WalletAssociation).where(
                    WalletAssociation.visitor_identity_id == visitor_identity_id,
                    WalletAssociation.revoked_at.is_(None),
                )
            )
        except SQLAlchemyError as lookup_error:
            raise WalletVerificationUnavailableError(
                "Could not resolve a wallet association conflict."
            ) from lookup_error
        if (
            existing_link is not None
            and (
                existing_link.visitor_identity_id != visitor_identity_id
                or existing_link.user_id != user_id
            )
        ) or (
            visitor_link is not None
            and (
                visitor_link.user_id != user_id
                or visitor_link.canonical_address != challenge_address
            )
        ):
            raise WalletAlreadyLinkedError("This wallet is already linked.") from exc
        raise WalletVerificationUnavailableError(
            "Could not save the verified wallet association."
        ) from exc
    except SQLAlchemyError as exc:
        await session.rollback()
        raise WalletVerificationUnavailableError(
            "Could not verify the wallet challenge."
        ) from exc
    return association
