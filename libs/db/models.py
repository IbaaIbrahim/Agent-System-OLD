"""SQLAlchemy models for the Agent System."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    BigInteger,
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


class PartnerStatus(str, Enum):
    """Partner status enumeration."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class Partner(Base, TimestampMixin):
    """Partner (owner) model for B2B2B white-label support.

    Each partner is a business that manages its own set of tenants.
    Platform owner (super admin) manages partners.
    """

    __tablename__ = "partners"
    __table_args__ = (
        Index("ix_partners_status", "status"),
        {"schema": "tenants"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    status: Mapped[PartnerStatus] = mapped_column(
        String(20),
        default=PartnerStatus.ACTIVE,
        nullable=False,
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Partner-level rate limits (caps for all tenants under this partner)
    rate_limit_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_tpm: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Billing: total credit pool for the partner in microdollars
    credit_balance_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    tenants: Mapped[list["Tenant"]] = relationship(back_populates="partner")
    api_keys: Mapped[list["PartnerApiKey"]] = relationship(back_populates="partner")
    plans: Mapped[list["PartnerPlan"]] = relationship(back_populates="partner")
    feature_configs: Mapped[list["PartnerFeatureConfig"]] = relationship(
        back_populates="partner"
    )


class PartnerApiKey(Base, TimestampMixin):
    """API key model for partner authentication (pk-agent-* prefix)."""

    __tablename__ = "partner_api_keys"
    __table_args__ = (
        Index("ix_partner_api_keys_key_hash", "key_hash"),
        Index("ix_partner_api_keys_partner_id", "partner_id"),
        {"schema": "tenants"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.partners.id", ondelete="CASCADE"),
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
    partner: Mapped["Partner"] = relationship(back_populates="api_keys")


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
        Index("ix_tenants_partner_id", "partner_id"),
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

    # Partner ownership (NULL = legacy/direct tenant managed by platform owner)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.partners.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Rate limits (override defaults)
    rate_limit_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_tpm: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    partner: Mapped["Partner | None"] = relationship(back_populates="tenants")
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
    tool_preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False, comment="User tool preferences"
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


# =============================================================================
# Billing: Partner Wallet & Deposits
# =============================================================================


class DepositStatus(str, Enum):
    """Deposit transaction status."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class PartnerWallet(Base, TimestampMixin):
    """Partner wallet for managing USD balance.

    Partners deposit money into their wallet, which is debited
    when their tenants consume LLM resources.
    """

    __tablename__ = "partner_wallets"
    __table_args__ = (
        Index("ix_partner_wallets_partner_id", "partner_id"),
        {"schema": "billing"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.partners.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    balance_micros: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    total_deposited_micros: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    total_spent_micros: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    low_balance_threshold_micros: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    last_low_balance_alert_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    partner: Mapped["Partner"] = relationship()
    deposits: Mapped[list["PartnerDeposit"]] = relationship(back_populates="wallet")


class PartnerDeposit(Base, TimestampMixin):
    """Record of money deposited into a partner wallet."""

    __tablename__ = "partner_deposits"
    __table_args__ = (
        Index("ix_partner_deposits_wallet_id", "wallet_id"),
        Index("ix_partner_deposits_status", "status"),
        {"schema": "billing"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing.partner_wallets.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[DepositStatus] = mapped_column(
        String(20), default=DepositStatus.PENDING, nullable=False
    )
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_transaction_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    wallet: Mapped["PartnerWallet"] = relationship(back_populates="deposits")


# =============================================================================
# Billing: Partner Plans & Tenant Subscriptions
# =============================================================================


class PartnerPlanStatus(str, Enum):
    """Partner plan status."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DRAFT = "draft"


class PartnerPlan(Base, TimestampMixin):
    """Subscription plan that partners define for their tenants.

    Each plan specifies monthly credits, extra credit pricing/lifetime,
    rate limits, features, and partner margin.
    """

    __tablename__ = "partner_plans"
    __table_args__ = (
        UniqueConstraint("partner_id", "slug", name="uq_partner_plans_partner_slug"),
        Index("ix_partner_plans_partner_id", "partner_id"),
        {"schema": "billing"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.partners.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    status: Mapped[PartnerPlanStatus] = mapped_column(
        String(20), default=PartnerPlanStatus.ACTIVE, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Credit allocation
    monthly_credits_micros: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    # Price per 1M extra credits in microdollars
    extra_credit_price_micros: Mapped[int] = mapped_column(
        BigInteger, default=1_000_000, nullable=False
    )
    # How long top-ups last (varies by plan tier)
    extra_credit_lifetime_days: Mapped[int] = mapped_column(
        Integer, default=365, nullable=False
    )

    # Rate limits (RPM/TPM)
    rate_limit_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_tpm: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Credit rate limits: {"tenant": {"hourly": N, "daily": N}, "user": {"hourly": N, "daily": N}}
    credit_rate_limits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # Feature limits: {"max_users": N, "max_concurrent_jobs": N, "tools_enabled": [...]}
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # Partner margin on LLM costs (percentage, e.g., 20.0 = 20%)
    margin_percent: Mapped[float] = mapped_column(
        Numeric(5, 2), default=0.0, nullable=False
    )

    # Billing cycle in days (default 30)
    billing_cycle_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)

    # Display order for pricing page
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    partner: Mapped["Partner"] = relationship()
    subscriptions: Mapped[list["TenantSubscription"]] = relationship(
        back_populates="plan"
    )


class SubscriptionStatus(str, Enum):
    """Tenant subscription status."""

    ACTIVE = "active"
    CANCELLED = "cancelled"
    PAST_DUE = "past_due"
    TRIAL = "trial"


class TenantSubscription(Base, TimestampMixin):
    """Links a tenant to their active subscription plan.

    Tracks billing period and remaining plan credits.
    """

    __tablename__ = "tenant_subscriptions"
    __table_args__ = (
        Index("ix_tenant_subscriptions_tenant_id", "tenant_id"),
        Index("ix_tenant_subscriptions_plan_id", "plan_id"),
        Index("ix_tenant_subscriptions_status", "status"),
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
        unique=True,  # One active subscription per tenant
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing.partner_plans.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        String(20), default=SubscriptionStatus.ACTIVE, nullable=False
    )

    # Billing period
    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Plan credits remaining for current period (resets monthly)
    plan_credits_remaining_micros: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )

    # Trial info
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Cancellation
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship()
    plan: Mapped["PartnerPlan"] = relationship(back_populates="subscriptions")
    top_ups: Mapped[list["CreditTopUp"]] = relationship(back_populates="subscription")


class TopUpStatus(str, Enum):
    """Credit top-up status."""

    ACTIVE = "active"
    DEPLETED = "depleted"
    EXPIRED = "expired"
    REFUNDED = "refunded"


class CreditTopUp(Base, TimestampMixin):
    """Additional credits purchased by a tenant.

    Top-ups are consumed in FIFO order after plan credits are exhausted.
    Lifetime is determined by the plan's extra_credit_lifetime_days.
    """

    __tablename__ = "credit_top_ups"
    __table_args__ = (
        Index("ix_credit_top_ups_tenant_id", "tenant_id"),
        Index("ix_credit_top_ups_status", "status"),
        Index("ix_credit_top_ups_expires_at", "expires_at"),
        # FIFO query index: active top-ups ordered by creation
        Index(
            "ix_credit_top_ups_tenant_fifo",
            "tenant_id",
            "status",
            "created_at",
        ),
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
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing.tenant_subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Credit amounts
    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remaining_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Cost charged to tenant
    price_paid_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Status and expiration
    status: Mapped[TopUpStatus] = mapped_column(
        String(20), default=TopUpStatus.ACTIVE, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Payment reference
    external_transaction_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship()
    subscription: Mapped["TenantSubscription | None"] = relationship(
        back_populates="top_ups"
    )


# =============================================================================
# Billing: System Features & Partner Configuration
# =============================================================================


class SystemFeature(Base, TimestampMixin):
    """Platform-defined feature with default model routing.

    Each feature (e.g., translation, RAG, document analysis) has a
    default LLM provider/model and cost weight multiplier.
    """

    __tablename__ = "system_features"
    __table_args__ = (
        Index("ix_system_features_slug", "slug"),
        {"schema": "billing"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Default model routing
    default_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    default_model_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # Cost multiplier (1.0 = base price, 1.5 = 50% markup)
    weight_multiplier: Mapped[float] = mapped_column(
        Numeric(5, 2), default=1.0, nullable=False
    )

    # Feature flags
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Relationships
    partner_configs: Mapped[list["PartnerFeatureConfig"]] = relationship(
        back_populates="feature"
    )


class PartnerFeatureConfig(Base, TimestampMixin):
    """Partner-level overrides for system features.

    Partners can customize model routing and pricing for each feature.
    """

    __tablename__ = "partner_feature_configs"
    __table_args__ = (
        UniqueConstraint(
            "partner_id", "feature_id", name="uq_partner_feature_configs_partner_feature"
        ),
        Index("ix_partner_feature_configs_partner_id", "partner_id"),
        {"schema": "billing"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.partners.id", ondelete="CASCADE"),
        nullable=False,
    )
    feature_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing.system_features.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Override model routing (NULL = use system defaults)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Override weight multiplier
    weight_multiplier: Mapped[float | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    # Partner can disable features
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    partner: Mapped["Partner"] = relationship()
    feature: Mapped["SystemFeature"] = relationship(back_populates="partner_configs")


class CreditUsageRecord(Base, TimestampMixin):
    """Detailed record of credit consumption for audit and rate limiting.

    Tracks both tenant cost (with margin) and partner cost (base LLM cost).
    """

    __tablename__ = "credit_usage_records"
    __table_args__ = (
        Index("ix_credit_usage_records_tenant_id", "tenant_id"),
        Index("ix_credit_usage_records_user_id", "user_id"),
        Index("ix_credit_usage_records_created_at", "created_at"),
        Index("ix_credit_usage_records_tenant_created", "tenant_id", "created_at"),
        Index("ix_credit_usage_records_user_created", "user_id", "created_at"),
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
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Feature used (if applicable)
    feature_slug: Mapped[str | None] = mapped_column(String(63), nullable=True)

    # Credit amounts
    credits_consumed_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plan_credits_used_micros: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    topup_credits_used_micros: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )

    # Partner cost (actual LLM cost without margin)
    partner_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Model info
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    tenant: Mapped["Tenant"] = relationship()
    user: Mapped["User | None"] = relationship()


class Conversation(Base, TimestampMixin):
    """Conversation model for grouping related jobs into a chat session."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_tenant_id", "tenant_id"),
        Index("ix_conversations_user_id", "user_id"),
        Index("ix_conversations_tenant_user", "tenant_id", "user_id"),
        Index("ix_conversations_updated_at", "updated_at"),
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
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict
    )

    # Relationships
    jobs: Mapped[list["Job"]] = relationship(back_populates="conversation")


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
        Index("ix_jobs_conversation_id", "conversation_id"),
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
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.conversations.id", ondelete="SET NULL"),
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
    conversation: Mapped["Conversation | None"] = relationship(back_populates="jobs")
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


class FileUpload(Base):
    """File upload model for storing uploaded file metadata.

    Files are temporarily stored in Redis (hot storage) with 15-minute TTL.
    When FILE_STORAGE_PERSIST is enabled, files are also written to disk.
    This model provides audit trail and conversation context persistence.
    """

    __tablename__ = "file_uploads"
    __table_args__ = (
        Index("ix_file_uploads_tenant_id", "tenant_id"),
        Index("ix_file_uploads_job_id", "job_id"),
        Index("ix_file_uploads_created_at", "created_at"),
        Index("ix_file_uploads_storage_key", "storage_key", unique=True),
        {"schema": "jobs"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.jobs.id", ondelete="CASCADE"),
        nullable=True,  # Can be null if file uploaded before job created
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
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Redis key (file:uuid) or disk path (disk:path/to/file)",
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        comment="Additional metadata like original path, upload source",
    )
    analysis_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Cached vision model analysis of the file content",
    )
    analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the file was analyzed by vision model",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class LiveSessionStatus(str, Enum):
    """Live session status enumeration."""

    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class LiveSession(Base, TimestampMixin):
    """Live assistant session for real-time voice + vision interaction."""

    __tablename__ = "live_sessions"
    __table_args__ = (
        Index("ix_live_sessions_tenant_id", "tenant_id"),
        Index("ix_live_sessions_user_id", "user_id"),
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
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.conversations.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Session configuration
    stt_provider: Mapped[str] = mapped_column(
        String(50), default="deepgram", nullable=False
    )
    tts_provider: Mapped[str] = mapped_column(
        String(50), default="elevenlabs", nullable=False
    )
    tts_voice_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)

    # Session state
    status: Mapped[LiveSessionStatus] = mapped_column(
        String(20), default=LiveSessionStatus.ACTIVE, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Usage tracking
    audio_input_seconds: Mapped[float] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )
    audio_output_seconds: Mapped[float] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )
    screen_frames_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    total_turns: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Metadata
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict
    )

    # Relationships
    conversation: Mapped["Conversation | None"] = relationship()


class KnowledgeBaseEntry(Base, TimestampMixin):
    """Knowledge base entry model for storing searchable content.

    Supports semantic search via Milvus vector embeddings.
    Multi-tenant isolation via tenant_id scoping.
    """

    __tablename__ = "knowledge_base_entries"
    __table_args__ = (
        Index("ix_kb_entries_tenant_id", "tenant_id"),
        Index("ix_kb_entries_category", "category"),
        Index("ix_kb_entries_tenant_category", "tenant_id", "category"),
        Index("ix_kb_entries_tags", "tags", postgresql_using="gin"),
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
        comment="User who created this entry",
    )

    # Core content
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Organization
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tags: Mapped[list[Any]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
        comment="List of tag strings",
    )

    # Metadata
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        comment="Additional metadata (source, author, etc.)",
    )

    # File references (screenshots, attachments)
    file_ids: Mapped[list[Any]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
        comment="List of FileUpload UUIDs referenced by this entry",
    )

    # Vector embedding tracking
    has_embedding: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Whether embedding has been generated and stored in Milvus",
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Model used for embedding generation",
    )
    embedding_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
