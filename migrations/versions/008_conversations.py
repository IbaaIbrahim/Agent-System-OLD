"""Create conversations table and add conversation_id to jobs

Revision ID: 008
Revises: 007
Create Date: 2026-02-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create conversations table in jobs schema
    op.create_table(
        "conversations",
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
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column(
            "is_archived",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
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
        schema="jobs",
    )

    # Create indexes for conversations
    op.create_index(
        "ix_conversations_tenant_id",
        "conversations",
        ["tenant_id"],
        schema="jobs",
    )
    op.create_index(
        "ix_conversations_user_id",
        "conversations",
        ["user_id"],
        schema="jobs",
    )
    op.create_index(
        "ix_conversations_tenant_user",
        "conversations",
        ["tenant_id", "user_id"],
        schema="jobs",
    )
    op.create_index(
        "ix_conversations_updated_at",
        "conversations",
        ["updated_at"],
        schema="jobs",
    )

    # Add updated_at trigger for conversations
    op.execute("""
        CREATE TRIGGER update_conversations_updated_at
        BEFORE UPDATE ON jobs.conversations
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)

    # Add conversation_id FK column to jobs table
    op.add_column(
        "jobs",
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        schema="jobs",
    )
    op.create_foreign_key(
        "fk_jobs_conversation_id",
        "jobs",
        "conversations",
        ["conversation_id"],
        ["id"],
        source_schema="jobs",
        referent_schema="jobs",
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_jobs_conversation_id",
        "jobs",
        ["conversation_id"],
        schema="jobs",
    )

    # Full-text search index on chat_messages.content for conversation search
    op.execute("""
        CREATE INDEX ix_chat_messages_content_fts
        ON jobs.chat_messages
        USING GIN (to_tsvector('english', COALESCE(content, '')))
    """)


def downgrade() -> None:
    # Drop full-text search index
    op.execute(
        "DROP INDEX IF EXISTS jobs.ix_chat_messages_content_fts"
    )

    # Drop conversation_id from jobs
    op.drop_index(
        "ix_jobs_conversation_id",
        table_name="jobs",
        schema="jobs",
    )
    op.drop_constraint(
        "fk_jobs_conversation_id",
        "jobs",
        schema="jobs",
        type_="foreignkey",
    )
    op.drop_column("jobs", "conversation_id", schema="jobs")

    # Drop conversations trigger
    op.execute(
        "DROP TRIGGER IF EXISTS update_conversations_updated_at "
        "ON jobs.conversations"
    )

    # Drop conversations table
    op.drop_index(
        "ix_conversations_updated_at",
        table_name="conversations",
        schema="jobs",
    )
    op.drop_index(
        "ix_conversations_tenant_user",
        table_name="conversations",
        schema="jobs",
    )
    op.drop_index(
        "ix_conversations_user_id",
        table_name="conversations",
        schema="jobs",
    )
    op.drop_index(
        "ix_conversations_tenant_id",
        table_name="conversations",
        schema="jobs",
    )
    op.drop_table("conversations", schema="jobs")
