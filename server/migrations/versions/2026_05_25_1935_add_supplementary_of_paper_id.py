"""add supplementary_of_paper_id to papers and paper_upload_jobs

Revision ID: b7c8d9e0f1a2
Revises: a4b5c6d7e8f9
Create Date: 2026-05-25 19:35:00.000000+00:00

A supplementary material is itself a Paper row that points back to its
"parent" paper via supplementary_of_paper_id. Library-list endpoints filter
these out so they don't appear as standalone items. The upload job carries
the parent id through the pipeline so the webhook can stamp the resulting
Paper row when the job completes. The FK lives on Paper (durable);
PaperUploadJob just records the intent (transient — no FK needed).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "papers",
        sa.Column(
            "supplementary_of_paper_id",
            UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_papers_supplementary_of_paper_id",
        "papers",
        ["supplementary_of_paper_id"],
    )
    op.add_column(
        "paper_upload_jobs",
        sa.Column(
            "supplementary_of_paper_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("paper_upload_jobs", "supplementary_of_paper_id")
    op.drop_index(
        "ix_papers_supplementary_of_paper_id",
        table_name="papers",
    )
    op.drop_column("papers", "supplementary_of_paper_id")
