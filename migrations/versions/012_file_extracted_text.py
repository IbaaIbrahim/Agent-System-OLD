"""Add extracted_text column to file_uploads table.

Stores raw extracted text from documents (PDF, DOCX, XLSX) so the main
agent can reference full document content across conversation turns.

Revision ID: 012
Revises: 011
Create Date: 2026-02-18 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "file_uploads",
        sa.Column(
            "extracted_text",
            sa.Text(),
            nullable=True,
            comment="Raw extracted text content from document (PDF, DOCX, XLSX)",
        ),
        schema="jobs",
    )


def downgrade() -> None:
    op.drop_column("file_uploads", "extracted_text", schema="jobs")
