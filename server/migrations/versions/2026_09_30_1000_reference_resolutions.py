"""reference_resolutions: cache for the reader's citation hover cards.

Revision ID: reference_resolutions_20260930
Revises: ingest_v2_data_20260927
Create Date: 2026-09-30 10:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "reference_resolutions_20260930"
down_revision: Union[str, None] = "ingest_v2_data_20260927"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reference_resolutions",
        # sha256 of the normalized entry text (app.references.text.cache_key)
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("entry_text", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # NULL = never (found); unresolved results get a week.
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("reference_resolutions")
