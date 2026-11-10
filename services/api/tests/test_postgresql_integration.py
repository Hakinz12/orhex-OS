"""Opt-in PostgreSQL integration tests against an isolated disposable schema.

Set ORHEX_TEST_POSTGRES_URL to a disposable PostgreSQL URL and
ORHEX_POSTGRES_TEST_CONFIRMATION to
I_CONFIRM_THIS_IS_A_DISPOSABLE_DATABASE before explicitly running this module.
The tests create and drop only a randomly named schema.
"""
import asyncio
import ipaddress
import os
import re
import socket
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import (
    _apply_startup_migrations,
    _initialize_schema,
    database_engine_configuration,
    normalize_database_url,
    postgresql_connection_target,
)
from app.models.domain import (
    AnonymousVisitorCredential,
    AnonymousVisitorIdentity,
    MemorySyncRecord,
    User,
    WalletAssociation,
    WalletVerificationChallenge,
    utc_now,
)

pytestmark = pytest.mark.postgres_integration
_DISPOSABLE_CONFIRMATION = "I_CONFIRM_THIS_IS_A_DISPOSABLE_DATABASE"
_PROTECTED_PRODUCTION_HOST_ENV = "ORHEX_PROTECTED_POSTGRES_HOST"


def _test_database_url(resolve_addresses=None):
    database_url = os.environ.get("ORHEX_TEST_POSTGRES_URL")
    confirmation = os.environ.get("ORHEX_POSTGRES_TEST_CONFIRMATION")
    if not database_url and not confirmation:
        pytest.skip("Explicit disposable PostgreSQL test configuration is absent.")
    if not database_url or confirmation != _DISPOSABLE_CONFIRMATION:
        raise RuntimeError(
            "PostgreSQL integration tests require a test URL and explicit disposable-database confirmation."
        )

    test_url = normalize_database_url(database_url)
    app_url = normalize_database_url(settings.DATABASE_URL)
    production_host = os.environ.get(_PROTECTED_PRODUCTION_HOST_ENV)
    selected_hostaddr = _assert_test_target_is_separate(
        test_url,
        app_url,
        production_host,
        resolve_addresses,
    )
    return test_url, selected_hostaddr


def _normalize_endpoint_host(host):
    if not isinstance(host, str) or not host or host != host.strip():
        raise ValueError("Invalid PostgreSQL endpoint host.")

    candidate = host[:-1] if host.endswith(".") else host
    if not candidate or candidate.endswith("."):
        raise ValueError("Invalid PostgreSQL endpoint host.")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        if (
            len(candidate) > 253
            or any(
                not re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
                    label,
                )
                for label in candidate.split(".")
            )
        ):
            raise ValueError("Invalid PostgreSQL endpoint host.")
        return candidate.casefold()

    return address.compressed


def _normalize_protected_production_host(host):
    try:
        normalized_host = _normalize_endpoint_host(host)
    except ValueError:
        raise RuntimeError(
            "The protected production PostgreSQL endpoint identity is missing or ambiguous."
        ) from None

    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        if (
            "." not in normalized_host
            or normalized_host == "localhost"
            or normalized_host.endswith(".localhost")
        ):
            raise RuntimeError(
                "The protected production PostgreSQL endpoint identity is missing or ambiguous."
            )
        return normalized_host

    if address.is_loopback or address.is_unspecified or address.is_multicast:
        raise RuntimeError(
            "The protected production PostgreSQL endpoint identity is missing or ambiguous."
        )
    return normalized_host


def _resolve_host_addresses(host, port):
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise RuntimeError(
            "Cannot establish that the PostgreSQL test target is separate."
        ) from None
    addresses = set()
    for result in results:
        address = ipaddress.ip_address(result[4][0].split("%", 1)[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        addresses.add(address.compressed)
    if not addresses:
        raise RuntimeError(
            "Cannot establish that the PostgreSQL test target is separate."
        )
    return addresses


def _normalize_resolved_addresses(addresses):
    try:
        normalized = set()
        for value in addresses:
            address = ipaddress.ip_address(value)
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            normalized.add(address.compressed)
    except (TypeError, ValueError):
        raise RuntimeError(
            "Cannot establish that the PostgreSQL test target is separate."
        ) from None
    if not normalized:
        raise RuntimeError(
            "Cannot establish that the PostgreSQL test target is separate."
        )
    return normalized


def _assert_test_target_is_separate(
    test_url,
    app_url,
    protected_production_host,
    resolve_addresses=None,
):
    try:
        test_host, test_port, _test_database = postgresql_connection_target(test_url)
    except ValueError:
        raise RuntimeError("The PostgreSQL test URL is invalid or ambiguous.") from None

    production_host = _normalize_protected_production_host(
        protected_production_host
    )
    try:
        normalized_test_host = _normalize_endpoint_host(test_host)
    except ValueError:
        raise RuntimeError("The PostgreSQL test URL is invalid or ambiguous.") from None
    if normalized_test_host == production_host:
        raise RuntimeError(
            "The PostgreSQL integration URL must not target the protected production endpoint."
        )

    resolver = resolve_addresses or _resolve_host_addresses
    try:
        test_addresses = _normalize_resolved_addresses(
            resolver(test_host, test_port)
        )
        production_addresses = _normalize_resolved_addresses(
            resolver(production_host, 5432)
        )
    except OSError:
        raise RuntimeError(
            "Cannot establish that the PostgreSQL test target is separate from production."
        ) from None
    if not test_addresses or not production_addresses:
        raise RuntimeError(
            "Cannot establish that the PostgreSQL test target is separate from production."
        )
    if test_addresses.intersection(production_addresses):
        raise RuntimeError(
            "The PostgreSQL integration URL must not resolve to the protected production endpoint."
        )

    if app_url.drivername != "sqlite+aiosqlite":
        try:
            app_host, app_port, _app_database = postgresql_connection_target(app_url)
        except ValueError:
            raise RuntimeError(
                "Cannot establish that the PostgreSQL test target is separate."
            ) from None

        try:
            app_addresses = _normalize_resolved_addresses(
                resolver(app_host, app_port)
            )
        except OSError:
            raise RuntimeError(
                "Cannot establish that the PostgreSQL test target is separate."
            ) from None
        if not app_addresses:
            raise RuntimeError(
                "Cannot establish that the PostgreSQL test target is separate."
            )
        if test_addresses.intersection(app_addresses):
            raise RuntimeError(
                "The PostgreSQL integration URL must target a separate server from the application database."
            )

    return min(
        test_addresses,
        key=lambda value: (
            ipaddress.ip_address(value).version,
            int(ipaddress.ip_address(value)),
        ),
    )


def _pinned_engine_configuration(database_url, hostaddr, *, pool_recycle=300):
    try:
        normalized_hostaddr = ipaddress.ip_address(hostaddr).compressed
    except ValueError:
        raise RuntimeError("The validated PostgreSQL address is invalid.") from None

    url, options = database_engine_configuration(
        database_url,
        app_env="test",
        pool_recycle=pool_recycle,
    )
    connect_args = dict(options.get("connect_args", {}))
    connect_args["hostaddr"] = normalized_hostaddr
    options["connect_args"] = connect_args
    return url, options


@pytest.mark.parametrize(
    "test_url",
    [
        "postgresql://test@127.0.0.1:5432/testdb?host=192.0.2.10",
        "postgresql://test@127.0.0.1:5432/testdb?port=6543",
        "postgresql://test@127.0.0.1:5432/testdb?hostaddr=192.0.2.10",
        "postgresql://test@127.0.0.1:5432/testdb?dbname=otherdb",
        "postgresql://test@127.0.0.1:5432/testdb?options=-csearch_path%3Dpublic",
        "postgresql://test@127.0.0.1:5432/testdb?server_settings=search_path%3Dpublic",
        "postgresql://test@127.0.0.1:5432/testdb?ssl=true",
    ],
)
def test_postgresql_test_target_rejects_query_overrides(test_url):
    with pytest.raises(RuntimeError, match="invalid or ambiguous"):
        _assert_test_target_is_separate(
            normalize_database_url(test_url),
            normalize_database_url("postgresql://app@127.0.0.1:5432/appdb"),
            "production.example",
            lambda _host, _port: {"127.0.0.1"},
        )


@pytest.mark.parametrize(
    "app_url",
    [
        "postgresql://app@127.0.0.1:5432/appdb?host=192.0.2.10",
        "postgresql://app@127.0.0.1:5432/appdb?port=6543",
        "postgresql://app@127.0.0.1:5432/appdb?database=otherdb",
    ],
)
def test_postgresql_test_guard_fails_closed_for_ambiguous_application_url(app_url):
    test_url = normalize_database_url(
        "postgresql://test@disposable.example:5432/testdb"
    )

    with pytest.raises(RuntimeError, match="Cannot establish"):
        _assert_test_target_is_separate(
            test_url,
            normalize_database_url(app_url),
            "production.example",
            lambda host, _port: (
                {"192.0.2.20"}
                if host == "production.example"
                else {"192.0.2.10"}
            ),
        )


def test_postgresql_test_target_rejects_dns_alias_of_application_database():
    test_url = normalize_database_url(
        "postgresql://test@disposable.example:5432/orhex"
    )
    app_url = normalize_database_url(
        "postgresql://app@production.example:5432/orhex"
    )

    def resolve_alias(host, _port):
        return (
            {"192.0.2.20"}
            if host == "protected.example"
            else {"192.0.2.10"}
        )

    with pytest.raises(RuntimeError, match="separate server"):
        _assert_test_target_is_separate(
            test_url,
            app_url,
            "protected.example",
            resolve_alias,
        )


def test_postgresql_test_target_fails_closed_when_dns_destination_is_unknown():
    test_url = normalize_database_url("postgresql://test@disposable.example/testdb")
    app_url = normalize_database_url("postgresql://app@production.example/appdb")

    def unresolved(host, _port):
        if host == "protected.example":
            raise socket.gaierror("production resolution unavailable")
        return {"192.0.2.10"}

    with pytest.raises(RuntimeError, match="Cannot establish"):
        _assert_test_target_is_separate(
            test_url,
            app_url,
            "protected.example",
            unresolved,
        )


def test_postgresql_test_target_rejects_different_database_on_same_server():
    test_url = normalize_database_url(
        "postgresql://test@disposable.example:5432/testdb"
    )
    app_url = normalize_database_url(
        "postgresql://app@production.example:5432/appdb"
    )
    with pytest.raises(RuntimeError, match="separate server"):
        _assert_test_target_is_separate(
            test_url,
            app_url,
            "protected.example",
            lambda host, _port: (
                {"192.0.2.10"}
                if host in {"disposable.example", "production.example"}
                else {"192.0.2.20"}
            ),
        )


@pytest.mark.parametrize(
    "protected_host",
    [
        None,
        "",
        "production.example:5432",
        "postgresql://production.example/db",
        "production.example,other.example",
        "localhost",
        "127.0.0.1",
    ],
)
def test_postgresql_test_guard_rejects_missing_or_ambiguous_protected_identity(
    protected_host,
):
    test_url = normalize_database_url(
        "postgresql://test@disposable.example:5432/testdb"
    )
    app_url = normalize_database_url("sqlite+aiosqlite:///./orhrex.db")

    with pytest.raises(RuntimeError, match="identity is missing or ambiguous"):
        _assert_test_target_is_separate(test_url, app_url, protected_host)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("disposable.example.test", "disposable.example.test"),
        ("Disposable.Example.Test", "disposable.example.test"),
        ("disposable.example.test.", "disposable.example.test"),
        ("192.0.2.10", "192.0.2.10"),
        ("2001:0DB8:0:0::1", "2001:db8::1"),
    ],
)
def test_postgresql_endpoint_host_normalization_accepts_valid_hosts(host, expected):
    assert _normalize_endpoint_host(host) == expected


@pytest.mark.parametrize(
    "host",
    [
        "",
        " disposable.example.test",
        "disposable..example.test",
        "disposable.example.test..",
        "-disposable.example.test",
        "disposable-.example.test",
        "disposable_example.test",
        "https://disposable.example.test",
    ],
)
def test_postgresql_endpoint_host_normalization_rejects_malformed_hosts(host):
    with pytest.raises(ValueError, match="Invalid PostgreSQL endpoint host"):
        _normalize_endpoint_host(host)


@pytest.mark.parametrize(
    ("test_host", "protected_host"),
    [
        ("192.0.2.10", "192.0.2.10"),
        ("[2001:0db8:0:0::1]", "2001:db8::1"),
        ("production.example.test.", "Production.Example.Test"),
    ],
)
def test_postgresql_test_guard_matches_normalized_production_hosts(
    test_host,
    protected_host,
):
    test_url = normalize_database_url(
        f"postgresql://test@{test_host}:5432/testdb"
    )
    app_url = normalize_database_url("sqlite+aiosqlite:///./orhrex.db")

    with pytest.raises(RuntimeError, match="protected production endpoint"):
        _assert_test_target_is_separate(
            test_url,
            app_url,
            protected_host,
            lambda _host, _port: pytest.fail("Exact host match should reject before DNS."),
        )


@pytest.mark.parametrize(
    "test_host",
    [
        "disposable..example.test",
        "disposable.example.test..",
        "-disposable.example.test",
        "disposable-.example.test",
        "disposable_example.test",
    ],
)
def test_postgresql_test_guard_rejects_malformed_target_hostname(test_host):
    test_url = normalize_database_url(
        f"postgresql://test@{test_host}:5432/testdb"
    )
    app_url = normalize_database_url("sqlite+aiosqlite:///./orhrex.db")

    with pytest.raises(RuntimeError, match="test URL is invalid or ambiguous"):
        _assert_test_target_is_separate(
            test_url,
            app_url,
            "production.example.test",
            lambda _host, _port: pytest.fail("Malformed host should reject before DNS."),
        )


def test_postgresql_test_guard_rejects_production_hostname_as_target():
    test_url = normalize_database_url(
        "postgresql://test@production.example:5432/other_database"
    )
    app_url = normalize_database_url("sqlite+aiosqlite:///./orhrex.db")

    with pytest.raises(RuntimeError, match="protected production endpoint"):
        _assert_test_target_is_separate(
            test_url,
            app_url,
            "PRODUCTION.EXAMPLE.",
            lambda _host, _port: {"192.0.2.10"},
        )


def test_postgresql_test_guard_rejects_dns_alias_of_production_endpoint():
    test_url = normalize_database_url(
        "postgresql://test@disposable-alias.example:5432/other_database"
    )
    app_url = normalize_database_url("sqlite+aiosqlite:///./orhrex.db")

    def resolve_alias(host, _port):
        return {"192.0.2.10"} if host in {
            "disposable-alias.example",
            "production.example",
        } else set()

    with pytest.raises(RuntimeError, match="resolve to the protected production"):
        _assert_test_target_is_separate(
            test_url,
            app_url,
            "production.example",
            resolve_alias,
        )


def test_postgresql_test_guard_fails_closed_when_production_dns_fails():
    test_url = normalize_database_url(
        "postgresql://test@disposable.example:5432/testdb"
    )
    app_url = normalize_database_url("sqlite+aiosqlite:///./orhrex.db")

    def unresolved(host, _port):
        if host == "production.example":
            raise socket.gaierror("production resolution unavailable")
        return {"192.0.2.10"}

    with pytest.raises(RuntimeError, match="Cannot establish"):
        _assert_test_target_is_separate(
            test_url,
            app_url,
            "production.example",
            unresolved,
        )


def test_postgresql_test_guard_allows_separate_disposable_endpoint_with_sqlite_app():
    test_url = normalize_database_url(
        "postgresql://test@disposable.example:5432/testdb"
    )
    app_url = normalize_database_url("sqlite+aiosqlite:///./orhrex.db")

    _assert_test_target_is_separate(
        test_url,
        app_url,
        "production.example",
        lambda host, _port: (
            {"192.0.2.10"}
            if host == "disposable.example"
            else {"192.0.2.20"}
        ),
    )


def test_postgresql_test_guard_selects_only_a_validated_nonproduction_address():
    test_url = normalize_database_url(
        "postgresql://test@disposable.example:5432/testdb"
    )
    app_url = normalize_database_url("sqlite+aiosqlite:///./orhrex.db")
    disposable_addresses = {"192.0.2.11", "192.0.2.10"}
    production_addresses = {"198.51.100.20"}

    def resolve(host, _port):
        if host == "disposable.example":
            return disposable_addresses
        return production_addresses

    selected_hostaddr = _assert_test_target_is_separate(
        test_url,
        app_url,
        "production.example",
        resolve,
    )

    assert selected_hostaddr in disposable_addresses
    assert selected_hostaddr not in production_addresses


def test_postgresql_test_guard_rejects_any_overlap_in_multiple_addresses():
    test_url = normalize_database_url(
        "postgresql://test@disposable.example:5432/testdb"
    )
    app_url = normalize_database_url("sqlite+aiosqlite:///./orhrex.db")
    address_sets = {
        "disposable.example": {"192.0.2.10", "192.0.2.20"},
        "production.example": {"192.0.2.20", "198.51.100.30"},
    }

    with pytest.raises(RuntimeError, match="protected production endpoint"):
        _assert_test_target_is_separate(
            test_url,
            app_url,
            "production.example",
            lambda host, _port: address_sets[host],
        )


def test_postgresql_pinned_engine_forwards_hostaddr_and_retains_tls_hostname(
    monkeypatch,
):
    database_url = normalize_database_url(
        "postgresql://test:password@disposable.example/neondb"
        "?sslmode=verify-full&channel_binding=require"
    )
    pinned_address = "192.0.2.10"
    engine_url, options = _pinned_engine_configuration(
        database_url,
        pinned_address,
    )
    engine = create_async_engine(engine_url, **options)
    captured = {}
    sentinel = object()

    def capture_connect(*args, **kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(engine.sync_engine.dialect, "connect", capture_connect)
    try:
        assert engine.sync_engine.pool._creator() is sentinel
    finally:
        asyncio.run(engine.dispose())

    assert captured["hostaddr"] == pinned_address
    assert captured["host"] == "disposable.example"
    assert captured["sslmode"] == "verify-full"
    assert engine_url.host == "disposable.example"


@pytest.mark.parametrize("resolution_result", [set(), OSError("DNS failed")])
@pytest.mark.asyncio
async def test_postgresql_fixture_fails_before_engine_creation_on_resolution_failure(
    monkeypatch,
    resolution_result,
):
    monkeypatch.setenv(
        "ORHEX_TEST_POSTGRES_URL",
        "postgresql://test@disposable.example/neondb",
    )
    monkeypatch.setenv(
        "ORHEX_POSTGRES_TEST_CONFIRMATION",
        _DISPOSABLE_CONFIRMATION,
    )
    monkeypatch.setenv(
        _PROTECTED_PRODUCTION_HOST_ENV,
        "production.example",
    )
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "sqlite+aiosqlite:///./orhrex.db",
    )
    if isinstance(resolution_result, OSError):
        def resolver(_host, _port):
            raise resolution_result
    else:
        def resolver(_host, _port):
            return resolution_result

    def unexpected_engine_creation(*_args, **_kwargs):
        pytest.fail("Engine creation must not occur before target validation.")

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_resolve_host_addresses", resolver)
    monkeypatch.setattr(module, "create_async_engine", unexpected_engine_creation)

    fixture_generator = postgres_schema.__wrapped__()
    with pytest.raises(RuntimeError, match="Cannot establish"):
        await fixture_generator.__anext__()


@pytest.fixture
async def postgres_schema():
    test_url, selected_hostaddr = _test_database_url()
    schema = f"orhex_it_{uuid.uuid4().hex}"
    admin_url, engine_options = _pinned_engine_configuration(
        test_url,
        selected_hostaddr,
    )
    admin_options = dict(engine_options)
    for option in (
        "pool_pre_ping",
        "pool_recycle",
        "pool_size",
        "max_overflow",
        "pool_timeout",
    ):
        admin_options.pop(option)
    admin_options["poolclass"] = NullPool
    admin_engine = create_async_engine(admin_url, echo=False, **admin_options)
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        schema_options = dict(engine_options)
        schema_connect_args = dict(schema_options.get("connect_args", {}))
        schema_connect_args["options"] = f"-csearch_path={schema},public"
        schema_options["connect_args"] = schema_connect_args
        engine = create_async_engine(admin_url, echo=False, **schema_options)
        try:
            yield engine, schema, admin_engine, test_url, selected_hostaddr
        finally:
            await engine.dispose()
    finally:
        try:
            async with admin_engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            await admin_engine.dispose()


async def _run_schema_migrations(engine):
    async with engine.begin() as connection:
        await connection.run_sync(_initialize_schema)


@pytest.mark.asyncio
async def test_driver_connection_and_fresh_schema_migrations_are_idempotent(postgres_schema):
    engine, _schema, _admin_engine, _test_url, _hostaddr = postgres_schema

    await _run_schema_migrations(engine)
    await _run_schema_migrations(engine)

    async with engine.connect() as connection:
        names = await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )
    expected_tables = {
        "users",
        "user_access_tokens",
        "anonymous_visitor_identities",
        "anonymous_visitor_credentials",
        "wallet_verification_challenges",
        "wallet_associations",
        "conversations",
        "conversation_messages",
        "goals",
        "commitments",
        "memory_sync_records",
        "sui_insights",
        "anticipation_insights",
        "tasks",
        "action_proposals",
        "orchestration_runs",
    }
    assert expected_tables <= names


@pytest.mark.asyncio
async def test_legacy_startup_migrations_preserve_rows_and_rerun(postgres_schema):
    engine, _schema, _admin_engine, _test_url, _hostaddr = postgres_schema
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            "CREATE TABLE users (id VARCHAR(64) PRIMARY KEY)"
        )
        await connection.exec_driver_sql(
            "INSERT INTO users (id) VALUES ('legacy-owner')"
        )
        await connection.exec_driver_sql(
            "CREATE TABLE memory_sync_records ("
            "id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64) NOT NULL, "
            "walrus_blob_id VARCHAR(256), namespace VARCHAR(128), "
            "status VARCHAR(32) NOT NULL, created_at TIMESTAMP)"
        )
        await connection.exec_driver_sql(
            "INSERT INTO memory_sync_records "
            "(id, user_id, walrus_blob_id, namespace, status, created_at) "
            "VALUES ('legacy-memory', 'legacy-owner', 'blob-1', 'legacy', "
            "'queued', CURRENT_TIMESTAMP)"
        )
        await connection.exec_driver_sql(
            "CREATE TABLE user_access_tokens ("
            "id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64), "
            "token_digest VARCHAR(64), created_at TIMESTAMP)"
        )
        await connection.exec_driver_sql(
            "INSERT INTO user_access_tokens "
            "(id, user_id, token_digest, created_at) VALUES "
            "('legacy-token', 'legacy-owner', 'digest', CURRENT_TIMESTAMP)"
        )
        await connection.exec_driver_sql(
            "CREATE TABLE conversations ("
            "id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64) NOT NULL, "
            "visitor_identity_id VARCHAR(64), version INTEGER NOT NULL DEFAULT 0, "
            "created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL)"
        )
        await connection.exec_driver_sql(
            "INSERT INTO conversations "
            "(id, user_id, created_at, updated_at) VALUES "
            "('legacy-conversation', 'legacy-owner', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )

        await connection.run_sync(_apply_startup_migrations)
        await connection.run_sync(_apply_startup_migrations)

        memory_columns = {
            column["name"]
            for column in await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_columns(
                    "memory_sync_records"
                )
            )
        }
        token_columns = {
            column["name"]
            for column in await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_columns(
                    "user_access_tokens"
                )
            )
        }
        conversation_columns = {
            column["name"]
            for column in await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_columns(
                    "conversations"
                )
            )
        }
        migration_result = await connection.execute(
            text(
                "SELECT m.status, m.idempotency_key, m.updated_at, "
                "t.expires_at, c.memory_context_status, c.memory_context_count "
                "FROM memory_sync_records AS m "
                "JOIN user_access_tokens AS t ON t.id = 'legacy-token' "
                "JOIN conversations AS c ON c.id = 'legacy-conversation' "
                "WHERE m.id = 'legacy-memory'"
            )
        )
        status, idempotency_key, updated_at, expires_at, context_status, context_count = (
            migration_result.one()
        )

    assert {"idempotency_key", "updated_at"} <= memory_columns
    assert "expires_at" in token_columns
    assert {"memory_context_status", "memory_context_count"} <= conversation_columns
    assert status == "pending"
    assert idempotency_key == "legacy-memory"
    assert updated_at is not None
    assert expires_at is not None
    assert context_status == "not_retrieved"
    assert context_count == 0


@pytest.mark.asyncio
async def test_foreign_keys_partial_unique_indexes_and_check_constraints(postgres_schema):
    engine, _schema, _admin_engine, _test_url, _hostaddr = postgres_schema
    await _run_schema_migrations(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        session.add(User(id="constraint-owner"))
        await session.commit()
        session.add(
            AnonymousVisitorIdentity(
                id="constraint-visitor",
                user_id="constraint-owner",
            )
        )
        await session.commit()

        session.add(
            AnonymousVisitorCredential(
                id="credential-active-1",
                visitor_id="constraint-visitor",
                credential_digest="digest-1",
                created_at=utc_now(),
            )
        )
        await session.commit()
        session.add(
            AnonymousVisitorCredential(
                id="credential-active-2",
                visitor_id="constraint-visitor",
                credential_digest="digest-2",
                created_at=utc_now(),
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        session.add(
            AnonymousVisitorCredential(
                id="credential-revoked",
                visitor_id="constraint-visitor",
                credential_digest="digest-revoked",
                created_at=utc_now(),
                revoked_at=utc_now(),
            )
        )
        await session.commit()

        session.add_all(
            [
                User(id="constraint-owner-2"),
                AnonymousVisitorIdentity(
                    id="constraint-visitor-2",
                    user_id="constraint-owner-2",
                ),
            ]
        )
        await session.commit()
        session.add(
            WalletAssociation(
                id="wallet-association-active-1",
                visitor_identity_id="constraint-visitor",
                user_id="constraint-owner",
                chain="sui",
                network="mainnet",
                canonical_address="0x1",
                verified_at=utc_now(),
                created_at=utc_now(),
            )
        )
        await session.commit()
        session.add(
            WalletAssociation(
                id="wallet-association-active-2",
                visitor_identity_id="constraint-visitor-2",
                user_id="constraint-owner-2",
                chain="sui",
                network="mainnet",
                canonical_address="0x1",
                verified_at=utc_now(),
                created_at=utc_now(),
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO anonymous_visitor_identities (id, user_id) "
                    "VALUES ('orphan-identity', 'missing-owner')"
                )
            )
        await session.rollback()

        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO memory_sync_records "
                    "(id, user_id, namespace, idempotency_key, text, status, updated_at) "
                    "VALUES ('bad-status', 'constraint-owner', 'test', 'bad', "
                    "'test', 'invalid', CURRENT_TIMESTAMP)"
                )
            )
        await session.rollback()


@pytest.mark.asyncio
async def test_transaction_commit_and_rollback(postgres_schema):
    engine, _schema, _admin_engine, _test_url, _hostaddr = postgres_schema
    await _run_schema_migrations(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        session.add(User(id="committed-owner"))
        await session.commit()
        session.add(User(id="rolled-back-owner"))
        await session.rollback()
        assert await session.get(User, "committed-owner") is not None
        assert await session.get(User, "rolled-back-owner") is None


async def _concurrent_commit(sessions, record):
    async with sessions() as session:
        session.add(record)
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            return False


@pytest.mark.asyncio
async def test_concurrent_challenge_consumption_is_one_time(postgres_schema):
    engine, _schema, _admin_engine, _test_url, _hostaddr = postgres_schema
    await _run_schema_migrations(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with sessions() as session:
        session.add(User(id="challenge-owner"))
        await session.commit()
        session.add(AnonymousVisitorIdentity(id="challenge-visitor", user_id="challenge-owner"))
        await session.commit()
        session.add(
            WalletVerificationChallenge(
                id="challenge-once",
                visitor_identity_id="challenge-visitor",
                user_id="challenge-owner",
                purpose="wallet_link",
                chain="sui",
                network="mainnet",
                canonical_address="0x1",
                nonce="nonce",
                nonce_hash="nonce-hash",
                message_digest="message-digest",
                created_at=now,
                expires_at=now + timedelta(minutes=5),
            )
        )
        await session.commit()

    async def consume():
        async with sessions() as session:
            result = await session.execute(
                update(WalletVerificationChallenge)
                .where(
                    WalletVerificationChallenge.id == "challenge-once",
                    WalletVerificationChallenge.consumed_at.is_(None),
                    WalletVerificationChallenge.expires_at > now,
                )
                .values(consumed_at=now)
            )
            await session.commit()
            return result.rowcount

    assert sum(await asyncio.gather(consume(), consume())) == 1


@pytest.mark.asyncio
async def test_concurrent_visitor_and_memory_uniqueness(postgres_schema):
    engine, _schema, _admin_engine, _test_url, _hostaddr = postgres_schema
    await _run_schema_migrations(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(User(id="concurrent-owner"))
        await session.commit()
        session.add(
            AnonymousVisitorIdentity(
                id="concurrent-visitor",
                user_id="concurrent-owner",
            )
        )
        await session.commit()

    visitor_results = await asyncio.gather(
        _concurrent_commit(
            sessions,
            AnonymousVisitorCredential(
                id="concurrent-credential-1",
                visitor_id="concurrent-visitor",
                credential_digest="concurrent-digest-1",
                created_at=utc_now(),
            ),
        ),
        _concurrent_commit(
            sessions,
            AnonymousVisitorCredential(
                id="concurrent-credential-2",
                visitor_id="concurrent-visitor",
                credential_digest="concurrent-digest-2",
                created_at=utc_now(),
            ),
        ),
    )
    assert sum(visitor_results) == 1

    memory_results = await asyncio.gather(
        _concurrent_commit(
            sessions,
            MemorySyncRecord(
                id="concurrent-memory-1",
                user_id="concurrent-owner",
                namespace="integration",
                idempotency_key="same-idempotency-key",
                text="first",
                status="pending",
                updated_at=utc_now(),
            ),
        ),
        _concurrent_commit(
            sessions,
            MemorySyncRecord(
                id="concurrent-memory-2",
                user_id="concurrent-owner",
                namespace="integration",
                idempotency_key="same-idempotency-key",
                text="second",
                status="pending",
                updated_at=utc_now(),
            ),
        ),
    )
    assert sum(memory_results) == 1


@pytest.mark.asyncio
async def test_pool_reconnects_after_idle_recycle(postgres_schema):
    _engine, schema, _admin_engine, test_url, selected_hostaddr = postgres_schema
    normalized_url, options = _pinned_engine_configuration(
        test_url,
        selected_hostaddr,
        pool_recycle=1,
    )
    connect_args = dict(options.get("connect_args", {}))
    connect_args["options"] = f"-csearch_path={schema},public"
    options.update(
        connect_args=connect_args,
        pool_size=1,
        max_overflow=0,
    )
    engine = create_async_engine(normalized_url, echo=False, **options)
    try:
        async with engine.connect() as connection:
            first = (
                await connection.execute(text("SELECT pg_backend_pid()"))
            ).scalar_one()
        await asyncio.sleep(1.1)
        async with engine.connect() as connection:
            second = (
                await connection.execute(text("SELECT pg_backend_pid()"))
            ).scalar_one()
        assert first != second
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pool_recovers_after_test_connection_is_terminated(postgres_schema):
    engine, _schema, admin_engine, _test_url, _hostaddr = postgres_schema
    async with engine.connect() as connection:
        backend_pid = (
            await connection.execute(text("SELECT pg_backend_pid()"))
        ).scalar_one()
        try:
            async with admin_engine.connect() as admin_connection:
                terminated = (
                    await admin_connection.execute(
                        text("SELECT pg_terminate_backend(:pid)"),
                        {"pid": backend_pid},
                    )
                ).scalar_one()
        except DBAPIError:
            pytest.skip("Disposable PostgreSQL user cannot terminate its own test connection.")

        if not terminated:
            pytest.skip("Disposable PostgreSQL server did not terminate the test connection.")
        with pytest.raises(DBAPIError):
            await connection.execute(text("SELECT 1"))

    async with engine.connect() as recovered:
        recovered_pid = (
            await recovered.execute(text("SELECT pg_backend_pid()"))
        ).scalar_one()
        assert recovered_pid != backend_pid
        assert (await recovered.execute(text("SELECT 1"))).scalar_one() == 1
