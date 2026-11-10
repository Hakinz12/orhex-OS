import asyncio

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import (
    database_engine_configuration,
    normalize_database_url,
    postgresql_connection_target,
)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgres://user:password@db.example.test/app",
        "postgresql://user:password@db.example.test/app",
        "postgresql+asyncpg://user:password@db.example.test/app",
        "postgresql+psycopg://user:password@db.example.test/app",
    ],
)
def test_postgresql_url_schemes_use_psycopg(database_url):
    normalized = normalize_database_url(database_url)

    assert normalized.drivername == "postgresql+psycopg"
    assert normalized.username == "user"
    assert normalized.password == "password"
    assert normalized.database == "app"


def test_postgresql_url_normalization_preserves_query_and_ipv6_authority():
    normalized = normalize_database_url(
        "postgresql://user:password@[2001:db8::1]:5433/app"
        "?application_name=orhex&sslmode=require"
    )

    assert normalized.drivername == "postgresql+psycopg"
    assert normalized.username == "user"
    assert normalized.password == "password"
    assert normalized.host == "2001:db8::1"
    assert normalized.port == 5433
    assert normalized.database == "app"
    assert normalized.query == {
        "application_name": "orhex",
        "sslmode": "require",
    }


def test_neon_connection_parameters_are_preserved_for_libpq():
    database_url = (
        "postgresql://user:password@ep-test.eu-west-2.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )

    assert postgresql_connection_target(database_url) == (
        "ep-test.eu-west-2.aws.neon.tech",
        5432,
        "neondb",
    )
    normalized, _options = database_engine_configuration(
        database_url,
        app_env="production",
    )

    assert normalized.query == {
        "sslmode": "require",
        "channel_binding": "require",
    }


@pytest.mark.parametrize(
    "parameter",
    [
        "channel_binding=disable",
        "channel_binding=prefer",
        "channel_binding=",
        "channel_binding=require&channel_binding=require",
        "sslmode=require&sslmode=require",
        "unknown_option=value",
        "host=other.example.test",
        "hostaddr=192.0.2.1",
        "port=6543",
        "dbname=other",
        "options=-csearch_path%3Dpublic",
        "connect_timeout=0",
        "connect_timeout=nope",
    ],
)
def test_postgresql_target_rejects_malformed_duplicate_and_ambiguous_parameters(
    parameter,
):
    database_url = (
        "postgresql://user:password@ep-test.eu-west-2.aws.neon.tech/neondb?"
        + parameter
    )

    with pytest.raises(ValueError):
        postgresql_connection_target(database_url)


def test_engine_configuration_normalizes_an_existing_url_object():
    database_url = make_url("postgresql://user:password@db.example.test/app")

    normalized, _options = database_engine_configuration(
        database_url,
        app_env="production",
    )

    assert normalized.drivername == "postgresql+psycopg"


def test_sqlite_url_and_default_path_are_preserved():
    database_url = "sqlite+aiosqlite:///./orhrex.db"

    normalized, options = database_engine_configuration(
        database_url,
        app_env="production",
    )

    assert normalized == make_url(database_url)
    assert normalized.database == "./orhrex.db"
    assert options == {"connect_args": {"check_same_thread": False}}


def test_postgresql_engine_uses_bounded_pool_and_stale_connection_checks():
    normalized, options = database_engine_configuration(
        "postgresql://user:password@db.example.test/app",
        app_env="production",
    )

    assert normalized.drivername == "postgresql+psycopg"
    assert options["pool_pre_ping"] is True
    assert options["pool_recycle"] == 300
    assert options["pool_size"] == 5
    assert options["max_overflow"] == 2
    assert options["pool_timeout"] == 30
    assert normalized.query["sslmode"] == "require"


def test_production_tls_preserves_libpq_sslmode():
    normalized, options = database_engine_configuration(
        "postgresql://user:password@db.example.test/app?sslmode=require",
        app_env="production",
    )

    assert normalized.query["sslmode"] == "require"
    assert options.get("connect_args", {}) == {}


def test_local_postgresql_is_not_forced_to_use_tls():
    normalized, options = database_engine_configuration(
        "postgresql://user:password@127.0.0.1/app",
        app_env="production",
    )

    assert normalized.drivername == "postgresql+psycopg"
    assert "sslmode" not in normalized.query
    assert options.get("connect_args", {}) == {}


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_sslmode_is_preserved_for_libpq_local_postgresql(host):
    normalized, options = database_engine_configuration(
        f"postgresql://user:password@{host}/app?sslmode=require",
        app_env="test",
    )

    assert normalized.query["sslmode"] == "require"
    assert options.get("connect_args", {}) == {}


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_production_local_postgresql_hosts_remain_available_without_forced_tls(host):
    normalized, options = database_engine_configuration(
        f"postgresql://user:password@{host}:5432/app",
        app_env="production",
    )

    assert "sslmode" not in normalized.query
    assert options.get("connect_args", {}) == {}


def test_production_local_postgresql_allows_explicit_tls_disable_for_disposable_tests():
    normalized, options = database_engine_configuration(
        "postgresql://user:password@localhost:5432/app?sslmode=disable",
        app_env="production",
    )

    assert normalized.query["sslmode"] == "disable"
    assert options.get("connect_args", {}) == {}


@pytest.mark.parametrize("sslmode", ["disable", "allow", "prefer"])
def test_production_external_postgresql_rejects_tls_downgrade(sslmode):
    with pytest.raises(ValueError, match="require TLS"):
        database_engine_configuration(
            "postgresql://user:password@db.example.test/app?sslmode=" + sslmode,
            app_env="production",
        )


@pytest.mark.parametrize(
    "sslmode",
    ["require", "verify-ca", "verify-full"],
)
def test_production_external_postgresql_preserves_secure_tls_mode(sslmode):
    normalized, _options = database_engine_configuration(
        "postgresql://user:password@db.example.test/app?sslmode=" + sslmode,
        app_env="production",
    )

    assert normalized.query["sslmode"] == sslmode


def test_production_tls_is_added_when_external_url_has_no_sslmode():
    normalized, _options = database_engine_configuration(
        "postgresql://user:password@db.example.test/app?channel_binding=require",
        app_env="production",
    )

    assert normalized.query == {
        "channel_binding": "require",
        "sslmode": "require",
    }


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql:///app",
        "postgresql://user:password@127.0.0.1/app?host=db.example.test",
        "postgresql://user:password@127.0.0.1/app?port=6543",
        "postgresql://user:password@127.0.0.1/app?ssl=true",
        "postgresql://user:password@127.0.0.1/app?server_settings=search_path%3Dpublic",
    ],
)
def test_postgresql_engine_rejects_missing_or_ambiguous_destinations(database_url):
    with pytest.raises(ValueError):
        database_engine_configuration(database_url, app_env="production")


def test_invalid_postgresql_sslmode_is_rejected():
    with pytest.raises(ValueError, match="unsupported PostgreSQL sslmode"):
        database_engine_configuration(
            "postgresql://user:password@db.example.test/app?sslmode=unknown",
            app_env="test",
        )


def test_psycopg_async_engine_constructs_without_connecting():
    import psycopg

    database_url, options = database_engine_configuration(
        "postgresql://user:password@db.example.test/app"
        "?sslmode=require&channel_binding=require",
        app_env="production",
    )

    engine = create_async_engine(database_url, **options)
    try:
        assert psycopg.__version__ == "3.3.8"
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
        assert engine.dialect.is_async
    finally:
        asyncio.run(engine.dispose())
