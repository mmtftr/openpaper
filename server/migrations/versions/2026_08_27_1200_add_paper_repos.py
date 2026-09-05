"""add paper_repos table for companion code repositories

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-08-27 12:00:00.000000+00:00

One companion GitHub repo per paper (UNIQUE paper_id, CASCADE on paper
deletion). The row is the index into an on-disk snapshot at
`{REPO_STORAGE_DIR}/{paper_id}/{commit_sha}/` plus the ingestion state
machine (`pending` → `ingesting` → `ready` | `error`). The papers table is
untouched.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_repos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "paper_id",
            UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("ref", sa.String(), nullable=True),
        sa.Column("commit_sha", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=True),
        sa.Column("total_bytes", sa.BigInteger(), nullable=True),
        sa.Column("storage_prefix", sa.String(), nullable=True),
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
    op.create_index("ix_paper_repos_paper_id", "paper_repos", ["paper_id"])


def downgrade() -> None:
    op.drop_index("ix_paper_repos_paper_id", table_name="paper_repos")
    op.drop_table("paper_repos")
