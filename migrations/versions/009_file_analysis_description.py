"""Add analysis_description column to file_uploads table

Revision ID: 009
Revises: 008
Create Date: 2026-02-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add analysis_description column to store cached vision analysis
    op.add_column(
        "file_uploads",
        sa.Column(
            "analysis_description",
            sa.Text(),
            nullable=True,
            comment="Cached vision model analysis of the file content",
        ),
        schema="jobs",
    )

    # Add analyzed_at timestamp so we know when the analysis was performed
    op.add_column(
        "file_uploads",
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp when the file was analyzed by vision model",
        ),
        schema="jobs",
    )


def downgrade() -> None:
    op.drop_column("file_uploads", "analyzed_at", schema="jobs")
    op.drop_column("file_uploads", "analysis_description", schema="jobs")
