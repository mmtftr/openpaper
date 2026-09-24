"""ingest v2 schema: stage queue, worker heartbeat, pages, figures.

Schema only (docs/INGEST_DESIGN.md §3). Existing papers' `papers.ocr` is
copied into `paper_pages` / `paper_figures` by a later, separate step; the
legacy columns (`ocr`, `raw_content`, `page_offset_map`, `parser`,
`upload_job_id`) stay until the switch-over.

Revision ID: ingest_v2_20260926
Revises: model_slots_20260925
Create Date: 2026-09-26 10:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ingest_v2_20260926"
down_revision: Union[str, None] = "model_slots_20260925"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def _paper_fk(**kw) -> sa.Column:
    return sa.Column(
        "paper_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        **kw,
    )


def upgrade() -> None:
    op.create_table(
        "ingest_stages",
        _paper_fk(primary_key=True),
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="pending"
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress_done", sa.Integer(), nullable=True),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_kind", sa.String(16), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'succeeded', 'skipped',"
            " 'failed', 'blocked')",
            name="ck_ingest_stages_status",
        ),
        sa.CheckConstraint(
            "error_kind IN ('temporary', 'rate_limited', 'permanent', 'config')",
            name="ck_ingest_stages_error_kind",
        ),
    )
    op.create_index(
        "ix_ingest_stages_due",
        "ingest_stages",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )

    op.create_table(
        "ingest_worker",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("hostname", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("id = 1", name="ck_ingest_worker_single"),
    )

    op.create_table(
        "paper_pages",
        _paper_fk(primary_key=True),
        sa.Column("page_no", sa.Integer(), primary_key=True),
        sa.Column("width_pt", sa.Float(), nullable=True),
        sa.Column("height_pt", sa.Float(), nullable=True),
        sa.Column("text_layer", sa.Text(), nullable=True),
        sa.Column("ocr_markdown", sa.Text(), nullable=True),
        sa.Column("ocr_payload", postgresql.JSONB(), nullable=True),
        sa.Column("ocr_quality", postgresql.JSONB(), nullable=True),
        sa.Column("repair_markdown", sa.Text(), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=True),
        sa.Column("markdown_source", sa.String(16), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("page_no >= 1", name="ck_paper_pages_page_no"),
        sa.CheckConstraint(
            "markdown_source IN ('ocr', 'ocr_repair', 'text_layer')",
            name="ck_paper_pages_source",
        ),
    )

    op.create_table(
        "paper_figures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _paper_fk(),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("ocr_image_id", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("bbox", postgresql.JSONB(), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "paper_id", "page_no", "ocr_image_id", name="uq_paper_figures_image"
        ),
    )
    op.create_index("ix_paper_figures_paper_id", "paper_figures", ["paper_id"])

    op.add_column("papers", sa.Column("arxiv_id", sa.Text(), nullable=True))
    op.add_column("papers", sa.Column("openalex_id", sa.Text(), nullable=True))
    op.add_column(
        "papers",
        sa.Column(
            "metadata_source",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.add_column(
        "highlights",
        sa.Column("origin", sa.Text(), nullable=False, server_default="user"),
    )
    op.create_check_constraint(
        "ck_highlights_origin", "highlights", "origin IN ('user', 'ai')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_highlights_origin", "highlights", type_="check")
    op.drop_column("highlights", "origin")
    op.drop_column("papers", "metadata_source")
    op.drop_column("papers", "openalex_id")
    op.drop_column("papers", "arxiv_id")
    op.drop_index("ix_paper_figures_paper_id", table_name="paper_figures")
    op.drop_table("paper_figures")
    op.drop_table("paper_pages")
    op.drop_table("ingest_worker")
    op.drop_index("ix_ingest_stages_due", table_name="ingest_stages")
    op.drop_table("ingest_stages")
