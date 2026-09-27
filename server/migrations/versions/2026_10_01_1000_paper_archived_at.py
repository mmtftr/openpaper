"""papers.archived_at: archived papers leave the library lists.

NULL = not archived (every existing paper). Archived papers stay openable;
only the list endpoints (`/api/paper/all`, `/active`, `/relevant`) filter
on it.

Revision ID: paper_archived_at_20261001
Revises: reference_resolutions_20260930
Create Date: 2026-10-01 10:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "paper_archived_at_20261001"
down_revision: Union[str, None] = "reference_resolutions_20260930"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "papers",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("papers", "archived_at")
