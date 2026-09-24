"""papers.source_filename / source_url: what an upload came from.

The ingest `metadata` stage looks for DOIs / arXiv ids in the original file
name and the URL a PDF was imported from (docs/INGEST_DESIGN.md §5). Both
are set by the upload route; existing papers keep NULL.

Revision ID: paper_source_20260928
Revises: ingest_v2_data_20260927
Create Date: 2026-09-28 10:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "paper_source_20260928"
down_revision: Union[str, None] = "ingest_v2_data_20260927"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("source_filename", sa.Text(), nullable=True))
    op.add_column("papers", sa.Column("source_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("papers", "source_url")
    op.drop_column("papers", "source_filename")
