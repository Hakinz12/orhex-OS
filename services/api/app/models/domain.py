"""SQLAlchemy ORM Domain Models for ORHREX OS."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    preferred_name = Column(String(128), nullable=True)
    reminder_style = Column(String(64), default="concise")  # concise, detailed, proactive
    sui_address = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Relationships
    goals = relationship("Goal", back_populates="user", cascade="all, delete-orphan")
    commitments = relationship("Commitment", back_populates="user", cascade="all, delete-orphan")
    memories = relationship("MemorySyncRecord", back_populates="user", cascade="all, delete-orphan")


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
    Tracks local application synchronization with Walrus Memory Mainnet.
    Walrus holds durable semantic blobs; this table stores application metadata,
    reconciliation states, and confirmation timestamps.
    """
    __tablename__ = "memory_sync_records"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    walrus_blob_id = Column(String(256), nullable=True, index=True)
    text = Column(Text, nullable=False)
    category = Column(String(64), default="personal")  # personal, goals, commitments, work
    memory_type = Column(String(64), default="semantic")  # semantic, episodic, procedural
    status = Column(String(32), default="queued")  # draft, queued, confirmed, failed
    confidence = Column(Float, default=1.0)
    source = Column(String(64), default="user_confirmed")
    created_at = Column(DateTime(timezone=True), default=utc_now)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)

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
