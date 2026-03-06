"""Create model pricing and usage ledger tables

Revision ID: 002
Revises: 001
Create Date: 2024-01-01 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create billing schema
    op.execute("CREATE SCHEMA IF NOT EXISTS billing")

    # Create model_pricing table
    op.create_table(
        "model_pricing",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("input_price_per_1k", sa.Numeric(10, 6), nullable=False),
        sa.Column("output_price_per_1k", sa.Numeric(10, 6), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
        sa.UniqueConstraint(
            "provider", "model_id", name="uq_model_pricing_provider_model"
        ),
        schema="billing",
    )

    # Create usage_ledger table (will add job_id FK after jobs table exists)
    op.create_table(
        "usage_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,  # FK added in migration 003
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Numeric(10, 6), nullable=False),
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
        "ix_usage_ledger_tenant_id",
        "usage_ledger",
        ["tenant_id"],
        schema="billing",
    )
    op.create_index(
        "ix_usage_ledger_created_at",
        "usage_ledger",
        ["created_at"],
        schema="billing",
    )
    op.create_index(
        "ix_usage_ledger_tenant_created",
        "usage_ledger",
        ["tenant_id", "created_at"],
        schema="billing",
    )

    # Create triggers for updated_at
    for table in ["model_pricing", "usage_ledger"]:
        op.execute(f"""
            CREATE TRIGGER update_{table}_updated_at
            BEFORE UPDATE ON billing.{table}
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        """)

    # Insert default model pricing
    op.execute("""
        INSERT INTO billing.model_pricing
        (id, provider, model_id, display_name, input_price_per_1k, output_price_per_1k, context_window)
        VALUES
        (gen_random_uuid(), 'anthropic', 'claude-sonnet-4-5', 'Claude Sonnet 4', 0.003, 0.015, 200000),
        (gen_random_uuid(), 'anthropic', 'claude-haiku-4-5', 'Claude Haiku 4', 0.003, 0.015, 200000),
        (gen_random_uuid(), 'anthropic', 'claude-opus-4-5', 'Claude Opus 4', 0.015, 0.075, 200000),
        (gen_random_uuid(), 'openai', 'gpt-4-vision-preview', 'GPT-4 Vision Preview', 0.01, 0.03, 128000),
        (gen_random_uuid(), 'openai', 'gpt-4-turbo', 'GPT-4 Turbo', 0.005, 0.015, 128000),
        (gen_random_uuid(), 'openai', 'gpt-4-turbo-2024-04-09', 'GPT-4 Turbo 2024-04-09', 0.00015, 0.0006, 128000),
        (gen_random_uuid(), 'openai', 'gpt-4o', 'GPT-4o', 0.005, 0.015, 128000),
        (gen_random_uuid(), 'openai', 'gpt-4o-mini', 'GPT-4o Mini', 0.00015, 0.0006, 128000),
        (gen_random_uuid(), 'openai', 'o4-mini', 'O4 Mini', 0.00015, 0.0006, 128000)
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    # Drop triggers
    for table in ["model_pricing", "usage_ledger"]:
        op.execute(f"DROP TRIGGER IF EXISTS update_{table}_updated_at ON billing.{table}")

    # Drop tables
    op.drop_table("usage_ledger", schema="billing")
    op.drop_table("model_pricing", schema="billing")

    # Drop schema
    op.execute("DROP SCHEMA IF EXISTS billing CASCADE")
