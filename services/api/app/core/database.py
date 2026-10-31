"""Database connection and session management for ORHEX OS."""
from typing import AsyncGenerator
from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.core.config import settings

# If user provided a postgresql:// URL, convert to async driver postgresql+asyncpg:// if needed
db_url = settings.DATABASE_URL
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif db_url.startswith("sqlite:///") and not db_url.startswith("sqlite+aiosqlite:///"):
    db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

# SQLite on Windows requires connect_args check_same_thread=False
connect_args = {}
if "sqlite" in db_url:
    connect_args["check_same_thread"] = False

engine = create_async_engine(
    db_url,
    echo=(settings.APP_ENV == "development"),
    connect_args=connect_args,
    future=True
)


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


if "sqlite" in db_url:
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


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing a transactional async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database tables."""
    import app.models.domain  # noqa: F401 - register models before create_all

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_memory_sync_records)
        await conn.run_sync(_migrate_user_access_tokens)
        await conn.run_sync(_migrate_user_memory_trash_retention)
