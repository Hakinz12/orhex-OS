"""Walrus Memory adapter backed by the official MemWal Python SDK."""
import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from memwal import (
    MemWal,
    RecallParams,
    MemWalError,
    MemWalRateLimited,
    MemWalRememberJobFailed,
    MemWalRememberJobTimeout,
)
from app.adapters.walrus.base import BaseWalrusMemory
from app.core.config import settings

logger = logging.getLogger("orhrex.walrus")

_client: Optional[MemWal] = None
_client_lock = asyncio.Lock()
_managed_clients: List[MemWal] = []


class WalrusAdapterError(RuntimeError):
    """A Walrus operation failed and cannot be represented as a successful result."""


def _configuration_error(
    private_key: str,
    account_id: str,
    server_url: str,
) -> Optional[str]:
    if not private_key or not account_id:
        return "Set MEMWAL_PRIVATE_KEY and MEMWAL_ACCOUNT_ID."
    if not re.fullmatch(r"[0-9a-fA-F]{64}", private_key):
        return "MEMWAL_PRIVATE_KEY must be 64 hexadecimal characters."
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", account_id):
        return "MEMWAL_ACCOUNT_ID must be 0x followed by 64 hexadecimal characters."

    try:
        parsed = urlsplit(server_url)
    except ValueError:
        return "MEMWAL_SERVER_URL must be an absolute HTTP or HTTPS URL."
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "MEMWAL_SERVER_URL must be an absolute HTTP or HTTPS URL."
    return None


def _effective_namespace(namespace: Optional[str]) -> str:
    return namespace or settings.MEMWAL_NAMESPACE or "orhrex-personal-memory"


def _safe_server_origin(server_url: str) -> Optional[str]:
    try:
        parsed = urlsplit(server_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        hostname = parsed.hostname
        if ":" in hostname:
            hostname = f"[{hostname}]"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{hostname}{port}"
    except ValueError:
        return None


def _build_client(
    private_key: Optional[str] = None,
    account_id: Optional[str] = None,
    server_url: Optional[str] = None,
    namespace: Optional[str] = None,
) -> Optional[MemWal]:
    key = settings.MEMWAL_PRIVATE_KEY if private_key is None else private_key
    account = settings.MEMWAL_ACCOUNT_ID if account_id is None else account_id
    url = settings.MEMWAL_SERVER_URL if server_url is None else server_url
    resolved_namespace = _effective_namespace(namespace)

    if _configuration_error(key, account, url):
        return None

    return MemWal.create(
        key=key,
        account_id=account,
        server_url=url,
        namespace=resolved_namespace,
    )


async def get_walrus_client() -> Optional[MemWal]:
    """Return the shared SDK client, creating it lazily from application settings."""
    global _client
    async with _client_lock:
        if _client is None:
            _client = _build_client()
            if _client is not None:
                _managed_clients.append(_client)
        return _client


async def close_walrus_client() -> None:
    """Close all SDK clients created by this module during application shutdown."""
    global _client
    async with _client_lock:
        clients = list(_managed_clients)
        if _client is not None and all(client is not _client for client in clients):
            clients.append(_client)
        _managed_clients.clear()
        _client = None

    errors = []
    for client in clients:
        try:
            await client.close()
        except Exception as exc:
            logger.error("Failed to close Walrus SDK client: %s", type(exc).__name__)
            errors.append(exc)
    if errors:
        raise errors[0]


class MemWalAdapter(BaseWalrusMemory):
    """Adapter preserving the existing Walrus memory service interface."""

    def __init__(
        self,
        server_url: Optional[str] = None,
        agent_id: Optional[str] = None,
        account_id: Optional[str] = None,
        private_key: Optional[str] = None,
        network: Optional[str] = None,
        namespace: Optional[str] = None,
        client: Optional[MemWal] = None,
    ):
        self.server_url = settings.MEMWAL_SERVER_URL if server_url is None else server_url
        self.agent_id = settings.MEMWAL_AGENT_ID if agent_id is None else agent_id
        self.account_id = settings.MEMWAL_ACCOUNT_ID if account_id is None else account_id
        self.private_key = settings.MEMWAL_PRIVATE_KEY if private_key is None else private_key
        self.network = settings.WALRUS_NETWORK if network is None else network
        self.namespace = _effective_namespace(namespace)
        self._client = client
        self._uses_settings = all(
            value is None
            for value in (server_url, account_id, private_key, namespace)
        )

    def _configuration_error(self) -> Optional[str]:
        return _configuration_error(self.private_key, self.account_id, self.server_url)

    async def _get_client(self) -> Optional[MemWal]:
        if self._client is not None:
            return self._client
        if self._uses_settings:
            return await get_walrus_client()

        async with _client_lock:
            if self._client is None:
                self._client = _build_client(
                    private_key=self.private_key,
                    account_id=self.account_id,
                    server_url=self.server_url,
                    namespace=self.namespace,
                )
                if self._client is not None:
                    _managed_clients.append(self._client)
            return self._client

    async def check_readiness(self) -> Dict[str, Any]:
        """Report credential validity and live relayer health without exposing secrets."""
        error = self._configuration_error()
        result: Dict[str, Any] = {
            "configured": error is None,
            "namespace": self.namespace,
            "network": self.network,
            "agent_id": self.agent_id or "not_set",
            "server_url": _safe_server_origin(self.server_url),
            "status": "initializing" if error is None else "unconfigured",
        }
        if error is not None:
            result["status"] = "unconfigured" if (
                not self.private_key or not self.account_id
            ) else "invalid_configuration"
            result["message"] = error
            return result

        try:
            client = await self._get_client()
        except Exception as exc:
            result["status"] = "init_failed"
            result["error"] = type(exc).__name__
            logger.error("Failed to initialize Walrus SDK client: %s", type(exc).__name__)
            return result
        if client is None:
            result["status"] = "init_failed"
            result["message"] = "Walrus SDK client could not be initialized."
            return result

        try:
            health = await client.health()
            result["status"] = "ready" if health.write_ready is True else "degraded"
            result["relayer_status"] = health.status
            result["relayer_version"] = health.relayer_version
            result["write_ready"] = health.write_ready
            result["mode"] = health.mode
        except MemWalRateLimited as exc:
            result["status"] = "rate_limited"
            result["error"] = type(exc).__name__
        except Exception as exc:
            result["status"] = "unreachable"
            result["error"] = type(exc).__name__
            logger.warning("Walrus health check failed: %s", type(exc).__name__)
        return result

    async def remember(
        self,
        namespace: str,
        text: str,
        category: str = "personal",
        memory_type: str = "semantic",
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write and wait for SDK confirmation.

        MemWal 0.2.1 stores text and namespace; category, memory_type, and
        metadata remain interface-level values and are not sent to the SDK.
        """
        if not text or not text.strip():
            return {
                "status": "failed",
                "error": "Memory text cannot be empty.",
                "definite_failure": True,
            }
        if len(text) > 8000:
            return {
                "status": "failed",
                "error": "Memory text exceeds 8000-character limit.",
                "definite_failure": True,
            }

        error = self._configuration_error()
        if error is not None:
            return {"status": "failed", "error": error, "definite_failure": True}

        try:
            client = await self._get_client()
        except Exception as exc:
            logger.error("Failed to initialize Walrus SDK client: %s", type(exc).__name__)
            return {
                "status": "failed",
                "error": "Walrus SDK client initialization failed.",
                "error_type": type(exc).__name__,
                "definite_failure": True,
            }
        if client is None:
            return {
                "status": "failed",
                "error": "Walrus SDK client could not be initialized.",
                "definite_failure": True,
            }

        try:
            result = await client.remember_and_wait(
                text=text,
                namespace=namespace,
                poll_interval_ms=1500,
                timeout_ms=90_000,
                idempotency_key=idempotency_key,
            )
            if not result.blob_id:
                return {
                    "status": "failed",
                    "error": "Walrus SDK returned without a confirmed blob ID.",
                }
            logger.info("Walrus write confirmed: namespace=%s", result.namespace)
            return {
                "status": "confirmed",
                "blob_id": result.blob_id,
                "memory_id": result.id,
                "namespace": result.namespace,
                "owner": result.owner,
                "category": category,
                "memory_type": memory_type,
            }
        except MemWalRememberJobTimeout as exc:
            logger.warning("Walrus write confirmation timed out.")
            return {
                "status": "timeout",
                "error": "Walrus storage confirmation timed out.",
                "error_type": type(exc).__name__,
                "job_id": exc.job_id,
            }
        except MemWalRateLimited as exc:
            logger.warning("Walrus memory write was rate limited.")
            return {
                "status": "rate_limited",
                "error": "Walrus relayer rate limit reached.",
                "error_type": type(exc).__name__,
                "definite_failure": True,
            }
        except MemWalRememberJobFailed as exc:
            logger.error("Walrus storage job failed: %s", type(exc).__name__)
            return {
                "status": "failed",
                "error": "Walrus storage job failed.",
                "error_type": type(exc).__name__,
                "job_id": exc.job_id,
                "definite_failure": True,
            }
        except MemWalError as exc:
            logger.error("Walrus SDK write failed: %s", type(exc).__name__)
            return {
                "status": "failed",
                "error": "Walrus SDK write failed.",
                "error_type": type(exc).__name__,
                "definite_failure": False,
            }
        except Exception as exc:
            logger.error("Walrus memory write failed: %s", type(exc).__name__)
            return {
                "status": "failed",
                "error": "Walrus memory write failed.",
                "error_type": type(exc).__name__,
                "definite_failure": False,
            }

    async def get_remember_status(self, job_id: str) -> Dict[str, Any]:
        """Read SDK remember-job status for safe reconciliation."""
        error = self._configuration_error()
        if error is not None:
            raise WalrusAdapterError(error)

        try:
            client = await self._get_client()
            if client is None:
                raise WalrusAdapterError("Walrus SDK client could not be initialized.")
            status = await client.get_remember_status(job_id)
        except WalrusAdapterError:
            raise
        except MemWalError as exc:
            logger.error("Walrus SDK status lookup failed: %s", type(exc).__name__)
            raise WalrusAdapterError("Walrus remember-job status lookup failed.") from exc
        except Exception as exc:
            logger.error("Walrus status lookup failed: %s", type(exc).__name__)
            raise WalrusAdapterError("Walrus remember-job status lookup failed.") from exc

        return {
            "job_id": status.job_id,
            "status": status.status,
            "blob_id": status.blob_id,
            "namespace": status.namespace,
            "error": status.error,
        }

    async def recall(
        self,
        namespace: str,
        query: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Return semantic search results from the SDK or surface the failure."""
        if not query or not query.strip():
            return []
        if limit < 1:
            raise ValueError("Recall limit must be at least 1.")

        error = self._configuration_error()
        if error is not None:
            raise WalrusAdapterError(error)

        try:
            client = await self._get_client()
            if client is None:
                raise WalrusAdapterError("Walrus SDK client could not be initialized.")
            result = await client.recall(
                RecallParams(
                    query=query,
                    limit=min(limit, 20),
                    namespace=namespace,
                )
            )
        except WalrusAdapterError:
            raise
        except MemWalRateLimited as exc:
            logger.warning("Walrus memory recall was rate limited.")
            raise WalrusAdapterError("Walrus memory recall was rate limited.") from exc
        except MemWalError as exc:
            logger.error("Walrus SDK recall failed: %s", type(exc).__name__)
            raise WalrusAdapterError("Walrus SDK recall failed.") from exc
        except Exception as exc:
            logger.error("Walrus memory recall failed: %s", type(exc).__name__)
            raise WalrusAdapterError("Walrus memory recall failed.") from exc

        return [
            {
                "blob_id": memory.blob_id,
                "text": memory.text,
                "distance": memory.distance,
                "score": memory.score,
                "created_at": memory.created_at,
                "namespace": namespace,
            }
            for memory in result.results
        ]

    async def get_blob_status(self, blob_id: str) -> Dict[str, Any]:
        """Report that SDK 0.2.1 does not provide direct blob-status lookup."""
        return {
            "blob_id": blob_id,
            "status": "unsupported",
            "message": (
                "memwal 0.2.1 does not expose blob-status lookup. "
                "A successful remember call confirms storage."
            ),
        }

    async def delete_memory(self, namespace: str, memory_id: str) -> Dict[str, Any]:
        """Report that SDK 0.2.1 does not provide memory deletion."""
        return {
            "success": False,
            "status": "unsupported",
            "error": "memwal 0.2.1 does not expose memory deletion.",
        }
