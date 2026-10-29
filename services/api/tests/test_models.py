"""Tests for database models and async operations."""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.database import Base
from app.models.domain import User, Goal, Commitment, MemorySyncRecord, SuiInsight


@pytest.fixture
async def test_db_session():
    """Create an in-memory SQLite database session for unit testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_user_and_goal(test_db_session: AsyncSession):
    """Verify user and associated goal creation."""
    user = User(
        preferred_name="Alice Builder",
        reminder_style="concise",
        sui_address="0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    )
    test_db_session.add(user)
    await test_db_session.flush()

    goal = Goal(
        user_id=user.id,
        title="Deploy ORHEX OS to Mainnet",
        progress_percent=25
    )
    test_db_session.add(goal)
    await test_db_session.flush()

    assert user.id is not None
    assert goal.id is not None
    assert goal.user_id == user.id


@pytest.mark.asyncio
async def test_create_memory_sync_record(test_db_session: AsyncSession):
    """Verify memory sync record persistence lifecycle."""
    user = User(preferred_name="Bob")
    test_db_session.add(user)
    await test_db_session.flush()

    mem = MemorySyncRecord(
        user_id=user.id,
        text="Target submission date is November 10, 2026.",
        category="commitments",
        memory_type="commitment",
        status="confirmed",
        walrus_blob_id="walrus_blob_test_881923"
    )
    test_db_session.add(mem)
    await test_db_session.flush()

    assert mem.id is not None
    assert mem.walrus_blob_id == "walrus_blob_test_881923"
    assert mem.status == "confirmed"

