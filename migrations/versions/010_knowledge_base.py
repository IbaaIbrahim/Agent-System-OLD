"""Add knowledge_base_entries table for RAG-capable knowledge storage.

Revision ID: 010
Revises: 009
Create Date: 2026-02-11 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create knowledge_base_entries table in jobs schema
    op.create_table(
        "knowledge_base_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("tags", JSONB, nullable=False, server_default="[]"),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("file_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("has_embedding", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("embedding_model", sa.String(100), nullable=True),
        sa.Column("embedding_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="jobs",
    )

    # Create indexes for efficient querying
    op.create_index("ix_kb_entries_tenant_id", "knowledge_base_entries", ["tenant_id"], schema="jobs")
    op.create_index("ix_kb_entries_category", "knowledge_base_entries", ["category"], schema="jobs")
    op.create_index("ix_kb_entries_tenant_category", "knowledge_base_entries", ["tenant_id", "category"], schema="jobs")

    # GIN index for tag array filtering
    op.create_index("ix_kb_entries_tags", "knowledge_base_entries", ["tags"], postgresql_using="gin", schema="jobs")


def downgrade() -> None:
    # Drop indexes in reverse order
    op.drop_index("ix_kb_entries_tags", table_name="knowledge_base_entries", schema="jobs")
    op.drop_index("ix_kb_entries_tenant_category", table_name="knowledge_base_entries", schema="jobs")
    op.drop_index("ix_kb_entries_category", table_name="knowledge_base_entries", schema="jobs")
    op.drop_index("ix_kb_entries_tenant_id", table_name="knowledge_base_entries", schema="jobs")

    # Drop table
    op.drop_table("knowledge_base_entries", schema="jobs")
