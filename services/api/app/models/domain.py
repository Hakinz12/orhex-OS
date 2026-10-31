"""SQLAlchemy ORM Domain Models for ORHEX OS."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    func,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from app.core.database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "memory_trash_retention_days IS NULL OR memory_trash_retention_days = 30",
            name="ck_users_memory_trash_retention_days",
        ),
    )

    id = Column(String(64), primary_key=True, default=generate_uuid)
    preferred_name = Column(String(128), nullable=True)
    reminder_style = Column(String(64), default="concise")  # concise, detailed, proactive
    sui_address = Column(String(128), nullable=True)
    memory_trash_retention_days = Column(Integer, nullable=True, default=None)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Relationships
    goals = relationship("Goal", back_populates="user", cascade="all, delete-orphan")
    commitments = relationship("Commitment", back_populates="user", cascade="all, delete-orphan")
    memories = relationship("MemorySyncRecord", back_populates="user", cascade="all, delete-orphan")
    access_tokens = relationship(
        "UserAccessToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserAccessToken(Base):
    """Revocable opaque API credential; the bearer value itself is never persisted."""
    __tablename__ = "user_access_tokens"
    __table_args__ = (
        Index("ix_user_access_tokens_user_id", "user_id"),
        Index("ix_user_access_tokens_expires_at", "expires_at"),
    )

    id = Column(String(64), primary_key=True, default=generate_uuid)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_digest = Column(String(64), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="access_tokens")


class Goal(Base):
    __tablename__ = "goals"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    target_date = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), default="active")  # active, completed, paused
    progress_percent = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user = relationship("User", back_populates="goals")
    commitments = relationship("Commitment", back_populates="goal")


class Commitment(Base):
    __tablename__ = "commitments"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    goal_id = Column(String(64), ForeignKey("goals.id"), nullable=True)
    text = Column(String(512), nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=True)
    recurrence = Column(String(32), nullable=True)  # daily, weekly, monthly
    is_completed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    user = relationship("User", back_populates="commitments")
    goal = relationship("Goal", back_populates="commitments")


class MemorySyncRecord(Base):
    """
    Durable local operation record for a memory write to Walrus.

    Walrus stores text and namespace; application metadata and ownership remain
    in this local record.
    """
    __tablename__ = "memory_sync_records"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'failed', "
            "'reconciliation_required')",
            name="ck_memory_sync_records_status",
        ),
        Index(
            "uq_memory_sync_records_user_idempotency",
            "user_id",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_memory_sync_records_user_status", "user_id", "status"),
        Index("ix_memory_sync_records_user_trash", "user_id", "trashed_at"),
    )

    id = Column(String(64), primary_key=True, default=generate_uuid)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    namespace = Column(
        String(128),
        nullable=False,
        default="orhrex-personal-memory",
        server_default="orhrex-personal-memory",
        index=True,
    )
    idempotency_key = Column(
        String(64),
        nullable=False,
        default=generate_uuid,
        server_default="",
    )
    walrus_blob_id = Column(String(256), nullable=True, index=True)
    walrus_memory_id = Column(String(256), nullable=True)
    walrus_job_id = Column(String(256), nullable=True)
    text = Column(Text, nullable=False)
    category = Column(String(64), default="personal")  # personal, goals, commitments, work
    memory_type = Column(String(64), default="semantic")  # semantic, episodic, procedural
    metadata_json = Column(Text, nullable=False, default="{}", server_default="{}")
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    last_error = Column(Text, nullable=True)
    confidence = Column(Float, default=1.0)
    source = Column(String(64), default="user_confirmed")
    created_at = Column(DateTime(timezone=True), default=utc_now)
    attempted_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    trashed_at = Column(DateTime(timezone=True), nullable=True)
    permanent_delete_started_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="memories")


class SuiInsight(Base):
    """Stores cached public Sui activity read via Tatum RPC."""
    __tablename__ = "sui_insights"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    sui_address = Column(String(128), nullable=False)
    network = Column(String(32), default="mainnet")
    balance_sui = Column(String(64), default="0")
    last_checkpoint = Column(Integer, nullable=True)
    label = Column(String(128), nullable=True)
    last_fetched_at = Column(DateTime(timezone=True), default=utc_now)
