"""Add tool preferences column to users table.

Revision ID: 006
Revises: 005
Create Date: 2024-01-06 00:00:00.000000

This migration adds:
- tool_preferences JSONB column to users table for storing user's tool settings
  Structure: {"enabled_tools": ["generate_checklist", "read_page", ...]}
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add tool_preferences JSONB column to users table
    op.add_column(
        "users",
        sa.Column(
            "tool_preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
            comment="User tool preferences: {enabled_tools: [...]}",
        ),
        schema="tenants",
    )


def downgrade() -> None:
    op.drop_column("users", "tool_preferences", schema="tenants")
