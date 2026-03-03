"""Add branching support to conversations.

Adds parent_message_id to chat_messages for conversation tree structure.
Adds active_branch to conversations for tracking which branch is displayed.

Revision ID: 013
Revises: 012
Create Date: 2026-03-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add parent_message_id for tree structure
    op.add_column(
        "chat_messages",
        sa.Column(
            "parent_message_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.chat_messages.id", ondelete="SET NULL"),
            nullable=True,
            comment="Parent message in conversation tree (NULL for root)",
        ),
        schema="jobs",
    )
    op.create_index(
        "ix_chat_messages_parent_message_id",
        "chat_messages",
        ["parent_message_id"],
        schema="jobs",
    )

    # Add active_branch to conversation for tracking selected branches
    op.add_column(
        "conversations",
        sa.Column(
            "active_branch",
            sa.dialects.postgresql.JSONB,
            server_default="{}",
            nullable=False,
            comment="Maps branch-point message IDs to active child message IDs",
        ),
        schema="jobs",
    )


def downgrade() -> None:
    op.drop_column("conversations", "active_branch", schema="jobs")
    op.drop_index(
        "ix_chat_messages_parent_message_id",
        table_name="chat_messages",
        schema="jobs",
    )
    op.drop_column("chat_messages", "parent_message_id", schema="jobs")
