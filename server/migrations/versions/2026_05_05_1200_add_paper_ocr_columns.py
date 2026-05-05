"""add paper ocr columns

Revision ID: e1f2a3b4c5d6
Revises: 0ceda25c2234
Create Date: 2026-05-05 12:00:00.000000+00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "0ceda25c2234"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add OCR-related columns to papers.

    parser: which extractor produced this paper's content. The chat layer
    branches on this — Mistral-parsed papers expose Adaptive/Comprehensive/
    Full modes; pymupdf-parsed papers fall back to Raw mode.
    ocr: full Mistral response with image_base64 stripped (per-page jsonb).
    figure_count / page_count: denormalized for cheap reads.
    """
    op.add_column(
        "papers",
        sa.Column("parser", sa.Text(), nullable=True),
    )
    op.add_column(
        "papers",
        sa.Column("ocr", JSONB(), nullable=True),
    )
    op.add_column(
        "papers",
        sa.Column("figure_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "papers",
        sa.Column("page_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("papers", "page_count")
    op.drop_column("papers", "figure_count")
    op.drop_column("papers", "ocr")
    op.drop_column("papers", "parser")
