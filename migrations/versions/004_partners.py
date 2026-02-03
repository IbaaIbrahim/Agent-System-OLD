"""Add partners and partner_api_keys tables for B2B2B multi-owner support

Revision ID: 004
Revises: 003
Create Date: 2024-01-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create partners table
    op.create_table(
        "partners",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(63), unique=True, nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("rate_limit_rpm", sa.Integer(), nullable=True),
        sa.Column("rate_limit_tpm", sa.Integer(), nullable=True),
        sa.Column("credit_balance_micros", sa.Integer(), nullable=True),
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
        schema="tenants",
    )

    # Index on partners status
    op.create_index(
        "ix_partners_status",
        "partners",
        ["status"],
        schema="tenants",
    )

    # Create partner_api_keys table
    op.create_table(
        "partner_api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "partner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.partners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
        schema="tenants",
    )

    # Indexes on partner_api_keys
    op.create_index(
        "ix_partner_api_keys_key_hash",
        "partner_api_keys",
        ["key_hash"],
        schema="tenants",
    )
    op.create_index(
        "ix_partner_api_keys_partner_id",
        "partner_api_keys",
        ["partner_id"],
        schema="tenants",
    )

    # Add partner_id column to tenants table (nullable for backward compat)
    op.add_column(
        "tenants",
        sa.Column(
            "partner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.partners.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema="tenants",
    )

    # Index on tenants.partner_id
    op.create_index(
        "ix_tenants_partner_id",
        "tenants",
        ["partner_id"],
        schema="tenants",
    )

    # Add updated_at triggers
    op.execute("""
        CREATE TRIGGER update_partners_updated_at
        BEFORE UPDATE ON tenants.partners
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)
    op.execute("""
        CREATE TRIGGER update_partner_api_keys_updated_at
        BEFORE UPDATE ON tenants.partner_api_keys
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)


def downgrade() -> None:
    # Drop triggers
    op.execute(
        "DROP TRIGGER IF EXISTS update_partner_api_keys_updated_at "
        "ON tenants.partner_api_keys"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS update_partners_updated_at "
        "ON tenants.partners"
    )

    # Drop index and column from tenants
    op.drop_index("ix_tenants_partner_id", table_name="tenants", schema="tenants")
    op.drop_column("tenants", "partner_id", schema="tenants")

    # Drop partner_api_keys table
    op.drop_index(
        "ix_partner_api_keys_partner_id",
        table_name="partner_api_keys",
        schema="tenants",
    )
    op.drop_index(
        "ix_partner_api_keys_key_hash",
        table_name="partner_api_keys",
        schema="tenants",
    )
    op.drop_table("partner_api_keys", schema="tenants")

    # Drop partners table
    op.drop_index("ix_partners_status", table_name="partners", schema="tenants")
    op.drop_table("partners", schema="tenants")
