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
    text,
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
    anonymous_visitor_identity = relationship(
        "AnonymousVisitorIdentity",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    wallet_verification_challenges = relationship(
        "WalletVerificationChallenge",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    wallet_associations = relationship(
        "WalletAssociation",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    conversations = relationship(
        "Conversation",
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


class AnonymousVisitorIdentity(Base):
    """Stable anonymous owner identity, separate from its rotating credentials."""
    __tablename__ = "anonymous_visitor_identities"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    user_id = Column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    user = relationship("User", back_populates="anonymous_visitor_identity")
    credentials = relationship(
        "AnonymousVisitorCredential",
        back_populates="visitor",
        cascade="all, delete-orphan",
    )
    wallet_verification_challenges = relationship(
        "WalletVerificationChallenge",
        back_populates="visitor",
        cascade="all, delete-orphan",
    )
    wallet_associations = relationship(
        "WalletAssociation",
        back_populates="visitor",
        cascade="all, delete-orphan",
    )


class AnonymousVisitorCredential(Base):
    """Digest-only, revocable credential for an anonymous visitor identity."""
    __tablename__ = "anonymous_visitor_credentials"
    __table_args__ = (
        Index("ix_anonymous_visitor_credentials_visitor_id", "visitor_id"),
        Index("ix_anonymous_visitor_credentials_expires_at", "expires_at"),
        Index(
            "uq_anonymous_visitor_credentials_active_visitor",
            "visitor_id",
            unique=True,
            sqlite_where=text("revoked_at IS NULL"),
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id = Column(String(64), primary_key=True, default=generate_uuid)
    visitor_id = Column(
        String(64),
        ForeignKey("anonymous_visitor_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    credential_digest = Column(String(64), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    replaced_by_credential_id = Column(
        String(64),
        ForeignKey(
            "anonymous_visitor_credentials.id",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )

    visitor = relationship(
        "AnonymousVisitorIdentity",
        back_populates="credentials",
        foreign_keys=[visitor_id],
    )


class WalletVerificationChallenge(Base):
    """One-time Sui wallet ownership challenge tied to a real visitor identity."""
    __tablename__ = "wallet_verification_challenges"
    __table_args__ = (
        Index("ix_wallet_verification_challenges_visitor_id", "visitor_identity_id"),
        Index("ix_wallet_verification_challenges_user_id", "user_id"),
        Index("ix_wallet_verification_challenges_expires_at", "expires_at"),
        Index("ix_wallet_verification_challenges_message_digest", "message_digest"),
    )

    id = Column(String(64), primary_key=True, default=generate_uuid)
    visitor_identity_id = Column(
        String(64),
        ForeignKey("anonymous_visitor_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    purpose = Column(String(32), nullable=False, index=True)
    chain = Column(String(32), nullable=False, default="sui")
    network = Column(String(32), nullable=False)
    canonical_address = Column(String(128), nullable=False)
    nonce = Column(String(128), nullable=False)
    nonce_hash = Column(String(128), nullable=False, index=True)
    message_digest = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    visitor = relationship(
        "AnonymousVisitorIdentity",
        back_populates="wallet_verification_challenges",
        foreign_keys=[visitor_identity_id],
    )
    user = relationship("User", back_populates="wallet_verification_challenges")


class WalletAssociation(Base):
    """Active, verified wallet link for one anonymous visitor identity."""
    __tablename__ = "wallet_associations"
    __table_args__ = (
        Index("ix_wallet_associations_visitor_id", "visitor_identity_id"),
        Index("ix_wallet_associations_user_id", "user_id"),
        Index("ix_wallet_associations_verified_at", "verified_at"),
        Index(
            "uq_wallet_associations_active_visitor",
            "visitor_identity_id",
            unique=True,
            sqlite_where=text("revoked_at IS NULL"),
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "uq_wallet_associations_active_wallet",
            "chain",
            "network",
            "canonical_address",
            unique=True,
            sqlite_where=text("revoked_at IS NULL"),
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id = Column(String(64), primary_key=True, default=generate_uuid)
    visitor_identity_id = Column(
        String(64),
        ForeignKey("anonymous_visitor_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    chain = Column(String(32), nullable=False, default="sui")
    network = Column(String(32), nullable=False)
    canonical_address = Column(String(128), nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    visitor = relationship(
        "AnonymousVisitorIdentity",
        back_populates="wallet_associations",
        foreign_keys=[visitor_identity_id],
    )
    user = relationship("User", back_populates="wallet_associations")


class Conversation(Base):
    """Conversation state with explicit visitor or PAT ownership."""
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
        Index(
            "ix_conversations_visitor_updated",
            "visitor_identity_id",
            "updated_at",
        ),
    )

    id = Column(String(64), primary_key=True, default=generate_uuid)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    visitor_identity_id = Column(
        String(64),
        ForeignKey("anonymous_visitor_identities.id", ondelete="CASCADE"),
        nullable=True,
    )
    version = Column(Integer, nullable=False, default=0, server_default="0")
    memory_context_status = Column(
        String(24), nullable=False, default="not_retrieved", server_default="not_retrieved"
    )
    memory_context_count = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    user = relationship("User", back_populates="conversations")
    visitor_identity = relationship("AnonymousVisitorIdentity")
    messages = relationship(
        "ConversationMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.sequence",
    )


class ConversationMessage(Base):
    """A persisted user or assistant turn, separate from long-term memory."""
    __tablename__ = "conversation_messages"
    __table_args__ = (
        Index(
            "uq_conversation_messages_sequence",
            "conversation_id",
            "sequence",
            unique=True,
        ),
        Index(
            "uq_conversation_messages_idempotency",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_conversation_messages_conversation_created", "conversation_id", "created_at"),
    )

    id = Column(String(64), primary_key=True, default=generate_uuid)
    conversation_id = Column(
        String(64),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence = Column(Integer, nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    idempotency_key = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    conversation = relationship("Conversation", back_populates="messages")


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


class AnticipationInsight(Base):
    """Deduplicated, explainable insight generated from owner-owned evidence."""
    __tablename__ = "anticipation_insights"
    __table_args__ = (
        Index("uq_anticipation_insights_user_fingerprint", "user_id", "fingerprint", unique=True),
        Index("ix_anticipation_insights_user_status_due", "user_id", "status", "due_at"),
    )

    id = Column(String(64), primary_key=True, default=generate_uuid)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    kind = Column(String(64), nullable=False)
    source_type = Column(String(32), nullable=False)
    source_id = Column(String(64), nullable=False)
    title = Column(String(256), nullable=False)
    explanation = Column(Text, nullable=False)
    priority = Column(String(16), nullable=False, default="low")
    due_at = Column(DateTime(timezone=True), nullable=True)
    evidence_json = Column(Text, nullable=False, default="{}")
    status = Column(String(16), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class Task(Base):
    """Owner-scoped actionable task with explicit lifecycle state."""
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("priority IN ('low', 'medium', 'high')", name="ck_tasks_priority"),
        CheckConstraint("status IN ('todo', 'in_progress', 'completed', 'cancelled')", name="ck_tasks_status"),
        Index("ix_tasks_user_status_due", "user_id", "status", "due_at"),
    )

    id = Column(String(64), primary_key=True, default=generate_uuid)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(String(16), nullable=False, default="medium")
    status = Column(String(16), nullable=False, default="todo")
    due_at = Column(DateTime(timezone=True), nullable=True)
    source_type = Column(String(32), nullable=True)
    source_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class ActionProposal(Base):
    """Prepared action requiring explicit approval before its supported effect."""
    __tablename__ = "action_proposals"
    __table_args__ = (
        CheckConstraint("status IN ('proposed', 'executed', 'dismissed')", name="ck_action_proposals_status"),
        Index("ix_action_proposals_user_status", "user_id", "status"),
        Index("uq_action_proposals_user_fingerprint", "user_id", "fingerprint", unique=True),
    )

    id = Column(String(64), primary_key=True, default=generate_uuid)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    action_type = Column(String(48), nullable=False)
    title = Column(String(256), nullable=False)
    rationale = Column(Text, nullable=False)
    payload_json = Column(Text, nullable=False, default="{}")
    status = Column(String(16), nullable=False, default="proposed")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    executed_at = Column(DateTime(timezone=True), nullable=True)


class OrchestrationRun(Base):
    """Auditable, idempotent record of one owner-scoped orchestration cycle."""
    __tablename__ = "orchestration_runs"
    __table_args__ = (
        Index("uq_orchestration_runs_user_idempotency", "user_id", "idempotency_key", unique=True),
        Index("ix_orchestration_runs_user_created", "user_id", "created_at"),
    )

    id = Column(String(64), primary_key=True, default=generate_uuid)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    idempotency_key = Column(String(64), nullable=False)
    trigger = Column(String(24), nullable=False, default="manual")
    status = Column(String(24), nullable=False, default="completed")
    summary_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
