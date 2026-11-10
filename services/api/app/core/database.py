"""Database connection and session management for ORHEX OS."""
import ipaddress
from typing import AsyncGenerator
from urllib.parse import parse_qsl, urlsplit
from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import declarative_base
from app.core.config import settings


_POSTGRESQL_QUERY_ALLOWLIST = {
    "application_name",
    "channel_binding",
    "connect_timeout",
    "sslmode",
}
_POSTGRESQL_SSLMODES = {
    "allow",
    "disable",
    "prefer",
    "require",
    "verify-ca",
    "verify-full",
}
_EXTERNAL_PRODUCTION_SSLMODES = {"require", "verify-ca", "verify-full"}


def normalize_database_url(database_url: str | URL) -> URL:
    """Normalize supported async database URLs without exposing credentials."""
    url = make_url(database_url) if isinstance(database_url, str) else database_url
    if url.drivername in {
        "postgres",
        "postgresql",
        "postgres+asyncpg",
        "postgresql+asyncpg",
        "postgres+psycopg",
        "postgresql+psycopg",
        "postgres+psycopg_async",
        "postgresql+psycopg_async",
    }:
        return url.set(drivername="postgresql+psycopg")
    if url.drivername == "sqlite":
        return url.set(drivername="sqlite+aiosqlite")
    return url


def postgresql_connection_target(database_url: str | URL) -> tuple[str, int, str]:
    """Return an unambiguous PostgreSQL target, rejecting query-based overrides."""
    if isinstance(database_url, str):
        try:
            raw_parameters = parse_qsl(
                urlsplit(database_url).query,
                keep_blank_values=True,
                strict_parsing=True,
            )
        except ValueError:
            raise ValueError(
                "DATABASE_URL contains malformed PostgreSQL parameters."
            ) from None
        raw_names = [name for name, _value in raw_parameters]
        if (
            len(raw_names) != len(set(raw_names))
            or any(
                name != name.casefold()
                or name not in _POSTGRESQL_QUERY_ALLOWLIST
                or not value
                for name, value in raw_parameters
            )
        ):
            raise ValueError(
                "DATABASE_URL contains unsupported or ambiguous PostgreSQL parameters."
            )

    url = normalize_database_url(database_url)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("A PostgreSQL Psycopg URL is required.")

    unsupported_parameters = {
        key.casefold()
        for key in url.query
        if key != key.casefold() or key.casefold() not in _POSTGRESQL_QUERY_ALLOWLIST
    }
    if unsupported_parameters or any(
        isinstance(value, tuple) for value in url.query.values()
    ):
        raise ValueError(
            "DATABASE_URL contains unsupported or ambiguous PostgreSQL parameters."
        )
    channel_binding = url.query.get("channel_binding")
    if channel_binding is not None and channel_binding != "require":
        raise ValueError("DATABASE_URL contains an unsupported channel_binding value.")
    sslmode = url.query.get("sslmode")
    if sslmode is not None and sslmode not in _POSTGRESQL_SSLMODES:
        raise ValueError("DATABASE_URL contains an unsupported PostgreSQL sslmode.")
    connect_timeout = url.query.get("connect_timeout")
    if connect_timeout is not None:
        try:
            if int(connect_timeout) <= 0:
                raise ValueError
        except ValueError:
            raise ValueError(
                "DATABASE_URL contains an invalid PostgreSQL connect_timeout."
            ) from None

    host = url.host
    if not host or "," in host or not url.database:
        raise ValueError("DATABASE_URL must specify one PostgreSQL host and database.")
    port = url.port or 5432
    if not 1 <= port <= 65535:
        raise ValueError("DATABASE_URL contains an invalid PostgreSQL port.")
    return host, port, url.database


def _is_local_database_host(host: str | None) -> bool:
    if (
        not host
        or host.casefold() == "localhost"
        or host.casefold().endswith(".localhost")
    ):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def database_engine_configuration(
    database_url: str | URL,
    *,
    app_env: str,
    pool_recycle: int = 300,
) -> tuple[URL, dict]:
    """Return a normalized URL and bounded, dialect-appropriate engine options.

    libpq's ``sslmode=require`` encrypts traffic but does not validate the
    server certificate or hostname; ``verify-ca`` validates the certificate
    chain, while ``verify-full`` also validates the hostname.
    """
    url = normalize_database_url(database_url)

    if url.drivername == "postgresql+psycopg":
        host, _port, _database = postgresql_connection_target(url)
        sslmode = url.query.get("sslmode")
        if isinstance(sslmode, tuple):
            raise ValueError("DATABASE_URL must not contain multiple sslmode values.")

        external_production_database = (
            app_env.strip().casefold() == "production"
            and not _is_local_database_host(host)
        )
        if external_production_database:
            if sslmode is not None and sslmode not in _EXTERNAL_PRODUCTION_SSLMODES:
                raise ValueError(
                    "External production PostgreSQL connections require TLS."
                )
            if sslmode is None:
                url = url.update_query_dict({"sslmode": "require"})

        return url, {
            "pool_pre_ping": True,
            "pool_recycle": pool_recycle,
            "pool_size": 5,
            "max_overflow": 2,
            "pool_timeout": 30,
        }

    if url.drivername == "sqlite+aiosqlite":
        return url, {"connect_args": {"check_same_thread": False}}

    return url, {}


db_url, _engine_options = database_engine_configuration(
    settings.DATABASE_URL,
    app_env=settings.APP_ENV,
)
engine = create_async_engine(
    db_url,
    echo=(settings.APP_ENV == "development"),
    future=True,
    **_engine_options,
)


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


if db_url.drivername == "sqlite+aiosqlite":
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

Base = declarative_base()


def _migrate_memory_sync_records(connection) -> None:
    """Add Phase 2 fields to databases created before memory operations were durable."""
    inspector = inspect(connection)
    if "memory_sync_records" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("memory_sync_records")}
    timestamp_type = (
        "TIMESTAMP WITH TIME ZONE"
        if connection.dialect.name == "postgresql"
        else "TIMESTAMP"
    )
    additions = {
        "namespace": "VARCHAR(128) NOT NULL DEFAULT 'orhrex-personal-memory'",
        "idempotency_key": "VARCHAR(64) NOT NULL DEFAULT ''",
        "walrus_memory_id": "VARCHAR(256)",
        "walrus_job_id": "VARCHAR(256)",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        "last_error": "TEXT",
        "attempted_at": timestamp_type,
        "updated_at": timestamp_type,
        "trashed_at": timestamp_type,
        "permanent_delete_started_at": timestamp_type,
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.exec_driver_sql(
                f"ALTER TABLE memory_sync_records ADD COLUMN {name} {definition}"
            )

    connection.exec_driver_sql(
        "UPDATE memory_sync_records SET idempotency_key = id "
        "WHERE idempotency_key = ''"
    )
    connection.exec_driver_sql(
        "UPDATE memory_sync_records SET updated_at = "
        "COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"
    )
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "ALTER TABLE memory_sync_records ALTER COLUMN updated_at "
            "SET DEFAULT CURRENT_TIMESTAMP"
        )
        connection.exec_driver_sql(
            "ALTER TABLE memory_sync_records ALTER COLUMN updated_at SET NOT NULL"
        )
    connection.exec_driver_sql(
        "UPDATE memory_sync_records SET status = 'pending' "
        "WHERE status IN ('queued', 'draft')"
    )
    connection.exec_driver_sql(
        "UPDATE memory_sync_records SET status = 'reconciliation_required' "
        "WHERE status NOT IN ('pending', 'confirmed', 'failed', "
        "'reconciliation_required')"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_memory_sync_records_user_idempotency "
        "ON memory_sync_records (user_id, idempotency_key)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_memory_sync_records_user_status "
        "ON memory_sync_records (user_id, status)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_memory_sync_records_user_id "
        "ON memory_sync_records (user_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_memory_sync_records_namespace "
        "ON memory_sync_records (namespace)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_memory_sync_records_user_trash "
        "ON memory_sync_records (user_id, trashed_at)"
    )

    if connection.dialect.name == "sqlite":
        allowed_statuses = (
            "'pending', 'confirmed', 'failed', 'reconciliation_required'"
        )
        for action in ("INSERT", "UPDATE"):
            trigger = f"trg_memory_sync_status_{action.lower()}"
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS {trigger} "
                f"BEFORE {action} ON memory_sync_records "
                f"WHEN NEW.status NOT IN ({allowed_statuses}) "
                "OR NEW.updated_at IS NULL "
                "BEGIN SELECT RAISE(ABORT, 'invalid memory sync record'); END"
            )
    elif connection.dialect.name == "postgresql":
        constraints = inspector.get_check_constraints("memory_sync_records")
        if not any(item["name"] == "ck_memory_sync_records_status" for item in constraints):
            connection.exec_driver_sql(
                "ALTER TABLE memory_sync_records ADD CONSTRAINT "
                "ck_memory_sync_records_status CHECK (status IN "
                "('pending', 'confirmed', 'failed', 'reconciliation_required'))"
            )


def _migrate_user_access_tokens(connection) -> None:
    """Backfill expiry for credentials created before mandatory token lifetimes."""
    inspector = inspect(connection)
    if "user_access_tokens" not in inspector.get_table_names():
        return
    columns = {
        column["name"]
        for column in inspector.get_columns("user_access_tokens")
    }
    if "expires_at" not in columns:
        timestamp_type = (
            "TIMESTAMP WITH TIME ZONE"
            if connection.dialect.name == "postgresql"
            else "TIMESTAMP"
        )
        connection.exec_driver_sql(
            "ALTER TABLE user_access_tokens "
            f"ADD COLUMN expires_at {timestamp_type}"
        )

    hours = settings.AUTH_TOKEN_TTL_HOURS
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(
            "UPDATE user_access_tokens SET expires_at = "
            f"datetime(COALESCE(created_at, CURRENT_TIMESTAMP), '+{hours} hours') "
            "WHERE expires_at IS NULL"
        )
        for action in ("INSERT", "UPDATE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS trg_user_token_expiry_{action.lower()} "
                f"BEFORE {action} ON user_access_tokens "
                "WHEN NEW.expires_at IS NULL "
                "BEGIN SELECT RAISE(ABORT, 'token expiry is required'); END"
            )
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "UPDATE user_access_tokens SET expires_at = "
            f"COALESCE(created_at, CURRENT_TIMESTAMP) + INTERVAL '{hours} hours' "
            "WHERE expires_at IS NULL"
        )
        connection.exec_driver_sql(
            "ALTER TABLE user_access_tokens ALTER COLUMN expires_at SET NOT NULL"
        )


def _migrate_user_memory_trash_retention(connection) -> None:
    """Add an opt-in retention setting; NULL keeps Trash user-controlled."""
    inspector = inspect(connection)
    if "users" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "memory_trash_retention_days" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN memory_trash_retention_days INTEGER"
        )
    if connection.dialect.name == "sqlite":
        for action in ("INSERT", "UPDATE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS "
                f"trg_users_memory_trash_retention_{action.lower()} "
                f"BEFORE {action} ON users "
                "WHEN NEW.memory_trash_retention_days IS NOT NULL "
                "AND NEW.memory_trash_retention_days != 30 "
                "BEGIN SELECT RAISE(ABORT, 'invalid memory trash retention'); END"
            )
    elif connection.dialect.name == "postgresql":
        constraints = inspector.get_check_constraints("users")
        if not any(
            item["name"] == "ck_users_memory_trash_retention_days"
            for item in constraints
        ):
            connection.exec_driver_sql(
                "ALTER TABLE users ADD CONSTRAINT "
                "ck_users_memory_trash_retention_days CHECK "
                "(memory_trash_retention_days IS NULL "
                "OR memory_trash_retention_days = 30)"
            )


def _migrate_anonymous_visitor_identity(connection) -> None:
    """Create additive visitor identity tables and indexes for existing databases."""
    inspector = inspect(connection)
    if "users" not in inspector.get_table_names():
        return

    identity_table = Base.metadata.tables["anonymous_visitor_identities"]
    credential_table = Base.metadata.tables["anonymous_visitor_credentials"]
    identity_table.create(connection, checkfirst=True)
    credential_table.create(connection, checkfirst=True)

    for table in (identity_table, credential_table):
        for index in table.indexes:
            index.create(connection, checkfirst=True)


def _migrate_wallet_verification(connection) -> None:
    """Create the additive wallet challenge and wallet association tables."""
    inspector = inspect(connection)
    if "users" not in inspector.get_table_names():
        return

    challenge_table = Base.metadata.tables["wallet_verification_challenges"]
    association_table = Base.metadata.tables["wallet_associations"]
    challenge_table.create(connection, checkfirst=True)
    association_table.create(connection, checkfirst=True)

    for table in (challenge_table, association_table):
        for index in table.indexes:
            index.create(connection, checkfirst=True)


def _migrate_conversations(connection) -> None:
    """Create additive conversation tables without changing existing records."""
    inspector = inspect(connection)
    if "users" not in inspector.get_table_names():
        return

    conversation_table = Base.metadata.tables["conversations"]
    message_table = Base.metadata.tables["conversation_messages"]
    conversation_table.create(connection, checkfirst=True)
    message_table.create(connection, checkfirst=True)

    for table in (conversation_table, message_table):
        for index in table.indexes:
            index.create(connection, checkfirst=True)

    columns = {
        column["name"]
        for column in inspect(connection).get_columns("conversations")
    }
    if "memory_context_status" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE conversations ADD COLUMN memory_context_status "
            "VARCHAR(24) NOT NULL DEFAULT 'not_retrieved'"
        )
    if "memory_context_count" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE conversations ADD COLUMN memory_context_count "
            "INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_orchestration_runs(connection) -> None:
    """Create the additive orchestration audit table and its indexes."""
    table = Base.metadata.tables["orchestration_runs"]
    table.create(connection, checkfirst=True)
    for index in table.indexes:
        index.create(connection, checkfirst=True)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing a transactional async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def _apply_startup_migrations(connection) -> None:
    """Apply the existing in-place migrations to the current schema."""
    _migrate_memory_sync_records(connection)
    _migrate_user_access_tokens(connection)
    _migrate_user_memory_trash_retention(connection)
    _migrate_anonymous_visitor_identity(connection)
    _migrate_wallet_verification(connection)
    _migrate_conversations(connection)
    Base.metadata.tables["anticipation_insights"].create(
        connection, checkfirst=True
    )
    _migrate_orchestration_runs(connection)


def _initialize_schema(connection) -> None:
    """Create current tables and apply the existing in-place migrations."""
    import app.models.domain  # noqa: F401 - register models before create_all

    Base.metadata.create_all(connection)
    _apply_startup_migrations(connection)


async def init_db() -> None:
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(_initialize_schema)
