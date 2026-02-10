"""Create file_uploads table for storing uploaded file metadata

Revision ID: 007
Revises: 006
Create Date: 2026-02-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create file_uploads table in jobs schema
    op.create_table(
        "file_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.jobs.id", ondelete="CASCADE"),
            nullable=True,  # Can be null if file uploaded before job created
        ),
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
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "storage_key",
            sa.String(255),
            nullable=False,
            comment="Redis key or future S3 path",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
            comment="Additional metadata like original path, upload source, etc.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="jobs",
    )

    # Create indexes for common queries
    op.create_index(
        "ix_file_uploads_tenant_id",
        "file_uploads",
        ["tenant_id"],
        schema="jobs",
    )
    op.create_index(
        "ix_file_uploads_job_id",
        "file_uploads",
        ["job_id"],
        schema="jobs",
    )
    op.create_index(
        "ix_file_uploads_created_at",
        "file_uploads",
        ["created_at"],
        schema="jobs",
    )
    op.create_index(
        "ix_file_uploads_storage_key",
        "file_uploads",
        ["storage_key"],
        unique=True,
        schema="jobs",
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_file_uploads_storage_key", table_name="file_uploads", schema="jobs")
    op.drop_index("ix_file_uploads_created_at", table_name="file_uploads", schema="jobs")
    op.drop_index("ix_file_uploads_job_id", table_name="file_uploads", schema="jobs")
    op.drop_index("ix_file_uploads_tenant_id", table_name="file_uploads", schema="jobs")

    # Drop table
    op.drop_table("file_uploads", schema="jobs")
