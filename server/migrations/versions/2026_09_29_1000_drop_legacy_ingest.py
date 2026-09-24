"""Drop the old pipeline's columns and the upload-jobs table (ingest v2 §10.6).

Separate from the data migration so the cutover can stop at
`paper_source_20260928`, run `python -m app.scripts.verify_ingest_migration
check` (it compares against these columns), and only then drop them:

- `papers.ocr`, `raw_content`, `page_offset_map`, `parser`, `figure_count`
  (the data now lives in `paper_pages` / `paper_figures`);
- `papers.upload_job_id` and the `paper_upload_jobs` table (uploads create
  the paper and its `ingest_stages` rows directly).

The old `paper_content_trigger()` (search vector from `raw_content`) was
already replaced by `ingest_v2_data_20260927`.

Downgrade re-creates the columns and the table empty; the dropped data is
not restored (use the pre-cutover backup).

Revision ID: drop_legacy_ingest_20260929
Revises: paper_source_20260928
Create Date: 2026-09-29 10:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "drop_legacy_ingest_20260929"
down_revision: Union[str, None] = "paper_source_20260928"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_COLUMNS = ("ocr", "raw_content", "page_offset_map", "parser", "figure_count")


def upgrade() -> None:
    op.drop_constraint("papers_upload_job_id_fkey", "papers", type_="foreignkey")
    op.drop_column("papers", "upload_job_id")
    for column in LEGACY_COLUMNS:
        op.drop_column("papers", column)
    op.drop_table("paper_upload_jobs")


def downgrade() -> None:
    op.create_table(
        "paper_upload_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column(
            "supplementary_of_paper_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.add_column("papers", sa.Column("raw_content", sa.Text(), nullable=True))
    op.add_column(
        "papers", sa.Column("page_offset_map", postgresql.JSONB(), nullable=True)
    )
    op.add_column("papers", sa.Column("parser", sa.Text(), nullable=True))
    op.add_column("papers", sa.Column("ocr", postgresql.JSONB(), nullable=True))
    op.add_column("papers", sa.Column("figure_count", sa.Integer(), nullable=True))
    op.add_column(
        "papers",
        sa.Column(
            "upload_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "paper_upload_jobs.id",
                name="papers_upload_job_id_fkey",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
