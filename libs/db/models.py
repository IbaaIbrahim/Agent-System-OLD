"""SQLAlchemy models for the Agent System."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }


class TimestampMixin:
    """Mixin for created_at and updated_at timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TenantStatus(str, Enum):
    """Tenant status enumeration."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class Tenant(Base, TimestampMixin):
    """Multi-tenant organization model."""

    __tablename__ = "tenants"
    __table_args__ = (
        Index("ix_tenants_status", "status"),
        {"schema": "tenants"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        String(20),
        default=TenantStatus.ACTIVE,
        nullable=False,
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # Rate limits (override defaults)
    rate_limit_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_tpm: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    users: Mapped[list["User"]] = relationship(back_populates="tenant")
    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="tenant")
    usage_records: Mapped[list["UsageLedger"]] = relationship(back_populates="tenant")
    jobs: Mapped[list["Job"]] = relationship(back_populates="tenant")


class UserRole(str, Enum):
    """User role enumeration."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class User(Base, TimestampMixin):
    """User model within a tenant (virtual users for B2B2B)."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        UniqueConstraint("tenant_id", "external_id", name="uq_users_tenant_external_id"),
        Index("ix_users_email", "email"),
        Index("ix_users_external_id", "external_id"),
        {"schema": "tenants"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Tenant's own user identifier",
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        String(20),
        default=UserRole.MEMBER,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict
    )

    # Custom rate limits (NULL = inherit from tenant)
    custom_rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    custom_tpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="users")


class ApiKey(Base, TimestampMixin):
    """API key model for tenant authentication."""

    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_key_hash", "key_hash"),
        Index("ix_api_keys_tenant_id", "tenant_id"),
        {"schema": "tenants"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    scopes: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="api_keys")


class ModelPricing(Base, TimestampMixin):
    """LLM model pricing configuration."""

    __tablename__ = "model_pricing"
    __table_args__ = (
        UniqueConstraint("provider", "model_id", name="uq_model_pricing_provider_model"),
        {"schema": "billing"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    input_price_per_1k: Mapped[float] = mapped_column(
        Numeric(10, 6), nullable=False
    )
    output_price_per_1k: Mapped[float] = mapped_column(
        Numeric(10, 6), nullable=False
    )
    context_window: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UsageLedger(Base, TimestampMixin):
    """Usage tracking for billing."""

    __tablename__ = "usage_ledger"
    __table_args__ = (
        Index("ix_usage_ledger_tenant_id", "tenant_id"),
        Index("ix_usage_ledger_created_at", "created_at"),
        Index("ix_usage_ledger_tenant_created", "tenant_id", "created_at"),
        {"schema": "billing"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="usage_records")


class JobStatus(str, Enum):
    """Job status enumeration."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(Base, TimestampMixin):
    """Agent job model."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_tenant_id", "tenant_id"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_tenant_status", "tenant_id", "status"),
        {"schema": "jobs"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[JobStatus] = mapped_column(
        String(20),
        default=JobStatus.PENDING,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict
    )

    # Completion info
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Token counts
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="jobs")
    snapshots: Mapped[list["JobSnapshot"]] = relationship(back_populates="job")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="job")


class JobSnapshot(Base, TimestampMixin):
    """Checkpoint snapshot for job state recovery."""

    __tablename__ = "job_snapshots"
    __table_args__ = (
        Index("ix_job_snapshots_job_id", "job_id"),
        {"schema": "jobs"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_num: Mapped[int] = mapped_column(Integer, nullable=False)
    state_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Relationships
    job: Mapped["Job"] = relationship(back_populates="snapshots")


class MessageRole(str, Enum):
    """Chat message role enumeration."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(Base, TimestampMixin):
    """Chat message model for conversation history."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_job_id", "job_id"),
        Index("ix_chat_messages_job_sequence", "job_id", "sequence_num"),
        {"schema": "jobs"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_num: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[MessageRole] = mapped_column(String(20), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict
    )

    # Token counts
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    job: Mapped["Job"] = relationship(back_populates="messages")
