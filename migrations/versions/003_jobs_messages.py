"""Create jobs, snapshots, and chat messages tables

Revision ID: 003
Revises: 002
Create Date: 2024-01-01 00:00:02.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create jobs schema
    op.execute("CREATE SCHEMA IF NOT EXISTS jobs")

    # Create jobs table
    op.create_table(
        "jobs",
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
            "status",
            sa.String(20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column(
            "tools_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("total_input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_output_tokens", sa.Integer(), server_default="0", nullable=False),
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
    op.create_index(
        "ix_jobs_tenant_id",
        "jobs",
        ["tenant_id"],
        schema="jobs",
    )
    op.create_index(
        "ix_jobs_status",
        "jobs",
        ["status"],
        schema="jobs",
    )
    op.create_index(
        "ix_jobs_tenant_status",
        "jobs",
        ["tenant_id", "status"],
        schema="jobs",
    )

    # Create job_snapshots table
    op.create_table(
        "job_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_num", sa.Integer(), nullable=False),
        sa.Column(
            "state_data",
            postgresql.JSONB(astext_type=sa.Text()),
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
    op.create_index(
        "ix_job_snapshots_job_id",
        "job_snapshots",
        ["job_id"],
        schema="jobs",
    )

    # Create chat_messages table
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_num", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "tool_calls",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("tool_call_id", sa.String(255), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
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
    op.create_index(
        "ix_chat_messages_job_id",
        "chat_messages",
        ["job_id"],
        schema="jobs",
    )
    op.create_index(
        "ix_chat_messages_job_sequence",
        "chat_messages",
        ["job_id", "sequence_num"],
        schema="jobs",
    )

    # Add FK from usage_ledger to jobs
    op.create_foreign_key(
        "fk_usage_ledger_job_id",
        "usage_ledger",
        "jobs",
        ["job_id"],
        ["id"],
        source_schema="billing",
        referent_schema="jobs",
        ondelete="SET NULL",
    )

    # Create triggers for updated_at
    for table in ["jobs", "job_snapshots", "chat_messages"]:
        op.execute(f"""
            CREATE TRIGGER update_{table}_updated_at
            BEFORE UPDATE ON jobs.{table}
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        """)


def downgrade() -> None:
    # Drop FK from usage_ledger
    op.drop_constraint(
        "fk_usage_ledger_job_id",
        "usage_ledger",
        schema="billing",
        type_="foreignkey",
    )

    # Drop triggers
    for table in ["jobs", "job_snapshots", "chat_messages"]:
        op.execute(f"DROP TRIGGER IF EXISTS update_{table}_updated_at ON jobs.{table}")

    # Drop tables
    op.drop_table("chat_messages", schema="jobs")
    op.drop_table("job_snapshots", schema="jobs")
    op.drop_table("jobs", schema="jobs")

    # Drop schema
    op.execute("DROP SCHEMA IF EXISTS jobs CASCADE")
