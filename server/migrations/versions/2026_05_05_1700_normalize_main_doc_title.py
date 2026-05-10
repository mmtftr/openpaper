"""normalize main doc title to 'main'

Revision ID: a4b5c6d7e8f9
Revises: f2a3b4c5d6e7
Create Date: 2026-05-05 17:00:00.000000+00:00

The agent's doc tools key off the doc title — `read_doc('main')` /
`write_doc('main', ...)` — so every MAIN row needs `title = 'main'`. Slice 1
seeded MAIN docs with the paper's own title, which left them un-addressable
under the new convention. One-shot UPDATE; no schema change.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE documents SET title = 'main' WHERE kind = 'main'")


def downgrade() -> None:
    # Original titles (paper title strings) aren't recoverable from this
    # migration — downgrade is a no-op. Acceptable: title is cosmetic for
    # MAIN docs, and the MAIN row itself isn't deleted.
    pass
