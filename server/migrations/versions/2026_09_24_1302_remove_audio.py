"""remove audio overviews

Revision ID: 71bec2bff5a0
Revises: 2e1bea163acc
Create Date: 2026-09-24 13:02:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op

revision: str = "71bec2bff5a0"
down_revision: Union[str, None] = "2e1bea163acc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # project_audio_overview references audio_overviews, so it goes first.
    op.drop_table("project_audio_overview")
    op.drop_table("audio_overviews")
    op.drop_table("audio_overview_jobs")


def downgrade() -> None:
    raise NotImplementedError("Audio overviews were removed; no downgrade.")
