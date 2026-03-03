"""Add comprehensive billing tables: wallets, plans, subscriptions, top-ups, features

Revision ID: 005
Revises: 004
Create Date: 2024-01-05 00:00:00.000000

This migration adds:
- partner_wallets: Partner USD balance management
- partner_deposits: Deposit transaction records
- partner_plans: Subscription tiers defined by partners
- tenant_subscriptions: Links tenants to plans with billing periods
- credit_top_ups: Additional purchased credits with FIFO consumption
- system_features: Platform-defined features with model routing
- partner_feature_configs: Partner overrides for features
- credit_usage_records: Detailed consumption audit trail
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================================
    # Partner Wallets
    # =========================================================================
    op.create_table(
        "partner_wallets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "partner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.partners.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "balance_micros",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "total_deposited_micros",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "total_spent_micros",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("low_balance_threshold_micros", sa.BigInteger(), nullable=True),
        sa.Column("last_low_balance_alert_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="billing",
    )
    op.create_index(
        "ix_partner_wallets_partner_id",
        "partner_wallets",
        ["partner_id"],
        schema="billing",
    )

    # =========================================================================
    # Partner Deposits
    # =========================================================================
    op.create_table(
        "partner_deposits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("billing.partner_wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount_micros", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("payment_method", sa.String(50), nullable=True),
        sa.Column("external_transaction_id", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="billing",
    )
    op.create_index(
        "ix_partner_deposits_wallet_id",
        "partner_deposits",
        ["wallet_id"],
        schema="billing",
    )
    op.create_index(
        "ix_partner_deposits_status",
        "partner_deposits",
        ["status"],
        schema="billing",
    )

    # =========================================================================
    # Partner Plans
    # =========================================================================
    op.create_table(
        "partner_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "partner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.partners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            server_default="active",
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        # Credit allocation
        sa.Column(
            "monthly_credits_micros",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "extra_credit_price_micros",
            sa.BigInteger(),
            server_default="1000000",  # $1 per 1M credits default
            nullable=False,
        ),
        sa.Column(
            "extra_credit_lifetime_days",
            sa.Integer(),
            server_default="365",
            nullable=False,
        ),
        # Rate limits
        sa.Column("rate_limit_rpm", sa.Integer(), nullable=True),
        sa.Column("rate_limit_tpm", sa.Integer(), nullable=True),
        # Credit rate limits (JSONB)
        sa.Column(
            "credit_rate_limits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        # Feature limits (JSONB)
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        # Partner margin
        sa.Column(
            "margin_percent",
            sa.Numeric(5, 2),
            server_default="0.0",
            nullable=False,
        ),
        sa.Column(
            "billing_cycle_days",
            sa.Integer(),
            server_default="30",
            nullable=False,
        ),
        sa.Column(
            "display_order",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="billing",
    )
    op.create_index(
        "ix_partner_plans_partner_id",
        "partner_plans",
        ["partner_id"],
        schema="billing",
    )
    op.create_unique_constraint(
        "uq_partner_plans_partner_slug",
        "partner_plans",
        ["partner_id", "slug"],
        schema="billing",
    )

    # =========================================================================
    # Tenant Subscriptions
    # =========================================================================
    op.create_table(
        "tenant_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.tenants.id", ondelete="CASCADE"),
            unique=True,  # One subscription per tenant
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("billing.partner_plans.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(20),
            server_default="active",
            nullable=False,
        ),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "plan_credits_remaining_micros",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="billing",
    )
    op.create_index(
        "ix_tenant_subscriptions_tenant_id",
        "tenant_subscriptions",
        ["tenant_id"],
        schema="billing",
    )
    op.create_index(
        "ix_tenant_subscriptions_plan_id",
        "tenant_subscriptions",
        ["plan_id"],
        schema="billing",
    )
    op.create_index(
        "ix_tenant_subscriptions_status",
        "tenant_subscriptions",
        ["status"],
        schema="billing",
    )

    # =========================================================================
    # Credit Top-Ups
    # =========================================================================
    op.create_table(
        "credit_top_ups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subscription_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("billing.tenant_subscriptions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount_micros", sa.BigInteger(), nullable=False),
        sa.Column("remaining_micros", sa.BigInteger(), nullable=False),
        sa.Column("price_paid_micros", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            server_default="active",
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("external_transaction_id", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="billing",
    )
    op.create_index(
        "ix_credit_top_ups_tenant_id",
        "credit_top_ups",
        ["tenant_id"],
        schema="billing",
    )
    op.create_index(
        "ix_credit_top_ups_status",
        "credit_top_ups",
        ["status"],
        schema="billing",
    )
    op.create_index(
        "ix_credit_top_ups_expires_at",
        "credit_top_ups",
        ["expires_at"],
        schema="billing",
    )
    # FIFO query index
    op.create_index(
        "ix_credit_top_ups_tenant_fifo",
        "credit_top_ups",
        ["tenant_id", "status", "created_at"],
        schema="billing",
    )

    # =========================================================================
    # System Features
    # =========================================================================
    op.create_table(
        "system_features",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(63), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_provider", sa.String(50), nullable=False),
        sa.Column("default_model_id", sa.String(100), nullable=False),
        sa.Column(
            "weight_multiplier",
            sa.Numeric(5, 2),
            server_default="1.0",
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "requires_approval",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="billing",
    )
    op.create_index(
        "ix_system_features_slug",
        "system_features",
        ["slug"],
        schema="billing",
    )

    # =========================================================================
    # Partner Feature Configs
    # =========================================================================
    op.create_table(
        "partner_feature_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "partner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.partners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "feature_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("billing.system_features.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("model_id", sa.String(100), nullable=True),
        sa.Column("weight_multiplier", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="billing",
    )
    op.create_index(
        "ix_partner_feature_configs_partner_id",
        "partner_feature_configs",
        ["partner_id"],
        schema="billing",
    )
    op.create_unique_constraint(
        "uq_partner_feature_configs_partner_feature",
        "partner_feature_configs",
        ["partner_id", "feature_id"],
        schema="billing",
    )

    # =========================================================================
    # Credit Usage Records
    # =========================================================================
    op.create_table(
        "credit_usage_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("feature_slug", sa.String(63), nullable=True),
        sa.Column("credits_consumed_micros", sa.BigInteger(), nullable=False),
        sa.Column(
            "plan_credits_used_micros",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "topup_credits_used_micros",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("partner_cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="billing",
    )
    op.create_index(
        "ix_credit_usage_records_tenant_id",
        "credit_usage_records",
        ["tenant_id"],
        schema="billing",
    )
    op.create_index(
        "ix_credit_usage_records_user_id",
        "credit_usage_records",
        ["user_id"],
        schema="billing",
    )
    op.create_index(
        "ix_credit_usage_records_created_at",
        "credit_usage_records",
        ["created_at"],
        schema="billing",
    )
    op.create_index(
        "ix_credit_usage_records_tenant_created",
        "credit_usage_records",
        ["tenant_id", "created_at"],
        schema="billing",
    )
    op.create_index(
        "ix_credit_usage_records_user_created",
        "credit_usage_records",
        ["user_id", "created_at"],
        schema="billing",
    )

    # =========================================================================
    # Updated_at Triggers
    # =========================================================================
    op.execute("""
        CREATE TRIGGER update_partner_wallets_updated_at
        BEFORE UPDATE ON billing.partner_wallets
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)
    op.execute("""
        CREATE TRIGGER update_partner_deposits_updated_at
        BEFORE UPDATE ON billing.partner_deposits
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)
    op.execute("""
        CREATE TRIGGER update_partner_plans_updated_at
        BEFORE UPDATE ON billing.partner_plans
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)
    op.execute("""
        CREATE TRIGGER update_tenant_subscriptions_updated_at
        BEFORE UPDATE ON billing.tenant_subscriptions
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)
    op.execute("""
        CREATE TRIGGER update_credit_top_ups_updated_at
        BEFORE UPDATE ON billing.credit_top_ups
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)
    op.execute("""
        CREATE TRIGGER update_system_features_updated_at
        BEFORE UPDATE ON billing.system_features
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)
    op.execute("""
        CREATE TRIGGER update_partner_feature_configs_updated_at
        BEFORE UPDATE ON billing.partner_feature_configs
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)
    op.execute("""
        CREATE TRIGGER update_credit_usage_records_updated_at
        BEFORE UPDATE ON billing.credit_usage_records
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)

    # =========================================================================
    # Seed System Features
    # =========================================================================
    op.execute("""
        INSERT INTO billing.system_features (id, slug, name, description, default_provider, default_model_id, weight_multiplier)
        VALUES
            (gen_random_uuid(), 'chat', 'General Chat', 'General purpose chat completion', 'openai', 'gpt-4o', 1.0),
            (gen_random_uuid(), 'translation', 'Translation', 'Text translation between languages', 'openai', 'gpt-4o-mini', 1.0),
            (gen_random_uuid(), 'summarization', 'Summarization', 'Document and text summarization', 'openai', 'gpt-4o-mini', 1.0),
            (gen_random_uuid(), 'rag', 'RAG Query', 'Retrieval-augmented generation queries', 'openai', 'gpt-4o-mini', 1.2),
            (gen_random_uuid(), 'checklist_generation', 'Checklist Generation', 'Generate structured checklists', 'openai', 'gpt-4o', 1.5),
            (gen_random_uuid(), 'document_analysis', 'Document Analysis', 'Analyze large documents', 'anthropic', 'claude-sonnet-4-5', 2.0),
            (gen_random_uuid(), 'code_generation', 'Code Generation', 'Generate and analyze code', 'anthropic', 'claude-sonnet-4-5', 1.5),
            (gen_random_uuid(), 'data_extraction', 'Data Extraction', 'Extract structured data from text', 'openai', 'gpt-4o', 1.2);
    """)


def downgrade() -> None:
    # Drop triggers
    op.execute(
        "DROP TRIGGER IF EXISTS update_credit_usage_records_updated_at "
        "ON billing.credit_usage_records"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS update_partner_feature_configs_updated_at "
        "ON billing.partner_feature_configs"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS update_system_features_updated_at "
        "ON billing.system_features"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS update_credit_top_ups_updated_at "
        "ON billing.credit_top_ups"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS update_tenant_subscriptions_updated_at "
        "ON billing.tenant_subscriptions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS update_partner_plans_updated_at "
        "ON billing.partner_plans"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS update_partner_deposits_updated_at "
        "ON billing.partner_deposits"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS update_partner_wallets_updated_at "
        "ON billing.partner_wallets"
    )

    # Drop credit_usage_records
    op.drop_index(
        "ix_credit_usage_records_user_created",
        table_name="credit_usage_records",
        schema="billing",
    )
    op.drop_index(
        "ix_credit_usage_records_tenant_created",
        table_name="credit_usage_records",
        schema="billing",
    )
    op.drop_index(
        "ix_credit_usage_records_created_at",
        table_name="credit_usage_records",
        schema="billing",
    )
    op.drop_index(
        "ix_credit_usage_records_user_id",
        table_name="credit_usage_records",
        schema="billing",
    )
    op.drop_index(
        "ix_credit_usage_records_tenant_id",
        table_name="credit_usage_records",
        schema="billing",
    )
    op.drop_table("credit_usage_records", schema="billing")

    # Drop partner_feature_configs
    op.drop_constraint(
        "uq_partner_feature_configs_partner_feature",
        "partner_feature_configs",
        schema="billing",
    )
    op.drop_index(
        "ix_partner_feature_configs_partner_id",
        table_name="partner_feature_configs",
        schema="billing",
    )
    op.drop_table("partner_feature_configs", schema="billing")

    # Drop system_features
    op.drop_index(
        "ix_system_features_slug",
        table_name="system_features",
        schema="billing",
    )
    op.drop_table("system_features", schema="billing")

    # Drop credit_top_ups
    op.drop_index(
        "ix_credit_top_ups_tenant_fifo",
        table_name="credit_top_ups",
        schema="billing",
    )
    op.drop_index(
        "ix_credit_top_ups_expires_at",
        table_name="credit_top_ups",
        schema="billing",
    )
    op.drop_index(
        "ix_credit_top_ups_status",
        table_name="credit_top_ups",
        schema="billing",
    )
    op.drop_index(
        "ix_credit_top_ups_tenant_id",
        table_name="credit_top_ups",
        schema="billing",
    )
    op.drop_table("credit_top_ups", schema="billing")

    # Drop tenant_subscriptions
    op.drop_index(
        "ix_tenant_subscriptions_status",
        table_name="tenant_subscriptions",
        schema="billing",
    )
    op.drop_index(
        "ix_tenant_subscriptions_plan_id",
        table_name="tenant_subscriptions",
        schema="billing",
    )
    op.drop_index(
        "ix_tenant_subscriptions_tenant_id",
        table_name="tenant_subscriptions",
        schema="billing",
    )
    op.drop_table("tenant_subscriptions", schema="billing")

    # Drop partner_plans
    op.drop_constraint(
        "uq_partner_plans_partner_slug",
        "partner_plans",
        schema="billing",
    )
    op.drop_index(
        "ix_partner_plans_partner_id",
        table_name="partner_plans",
        schema="billing",
    )
    op.drop_table("partner_plans", schema="billing")

    # Drop partner_deposits
    op.drop_index(
        "ix_partner_deposits_status",
        table_name="partner_deposits",
        schema="billing",
    )
    op.drop_index(
        "ix_partner_deposits_wallet_id",
        table_name="partner_deposits",
        schema="billing",
    )
    op.drop_table("partner_deposits", schema="billing")

    # Drop partner_wallets
    op.drop_index(
        "ix_partner_wallets_partner_id",
        table_name="partner_wallets",
        schema="billing",
    )
    op.drop_table("partner_wallets", schema="billing")
