"""Add live_sessions table for real-time voice + vision assistant.

Revision ID: 011
Revises: 010
Create Date: 2026-02-11 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "live_sessions",
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
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Session configuration
        sa.Column("stt_provider", sa.String(50), nullable=False, server_default="deepgram"),
        sa.Column("tts_provider", sa.String(50), nullable=False, server_default="elevenlabs"),
        sa.Column("tts_voice_id", sa.String(100), nullable=True),
        sa.Column("language", sa.String(10), nullable=False, server_default="en"),
        # Session state
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        # Usage tracking
        sa.Column("audio_input_seconds", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("audio_output_seconds", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("screen_frames_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_turns", sa.Integer(), nullable=False, server_default="0"),
        # Metadata
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
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

    op.create_index(
        "ix_live_sessions_tenant_id",
        "live_sessions",
        ["tenant_id"],
        schema="jobs",
    )
    op.create_index(
        "ix_live_sessions_user_id",
        "live_sessions",
        ["user_id"],
        schema="jobs",
    )
    op.create_index(
        "ix_live_sessions_status",
        "live_sessions",
        ["status"],
        schema="jobs",
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("ix_live_sessions_status", table_name="live_sessions", schema="jobs")
    op.drop_index("ix_live_sessions_user_id", table_name="live_sessions", schema="jobs")
    op.drop_index("ix_live_sessions_tenant_id", table_name="live_sessions", schema="jobs")
    op.drop_table("live_sessions", schema="jobs")
