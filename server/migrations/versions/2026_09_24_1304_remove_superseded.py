"""remove superseded tables and columns

Drops paper_notes (replaced by documents), paper_images (image extraction
was removed from the jobs pipeline), paper_passages and its tsvector trigger
function (nothing searched it), and papers.summary / summary_citations /
starter_questions (no longer generated or displayed).

Revision ID: a3b99627a39f
Revises: 5cec2d370cbe
Create Date: 2026-09-24 13:04:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a3b99627a39f"
down_revision: Union[str, None] = "5cec2d370cbe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("paper_notes")
    op.drop_table("paper_images")

    # Dropping the table drops its indexes and the paper_passages_tsvectorupdate
    # trigger; the trigger function is a standalone object and goes separately.
    op.drop_table("paper_passages")
    op.execute("DROP FUNCTION IF EXISTS paper_passages_tsvector_trigger()")

    op.drop_column("papers", "summary")
    op.drop_column("papers", "summary_citations")
    op.drop_column("papers", "starter_questions")


def downgrade() -> None:
    raise NotImplementedError("remove_superseded is not reversible")
