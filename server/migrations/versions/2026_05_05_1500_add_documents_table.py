"""add documents table

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-05-05 15:00:00.000000+00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the `documents` table.

    Shaped once for the full end state (paper-scoped folders + root-level user
    docs + cross-references); slice 1 only fills MAIN rows. The partial unique
    index enforces "at most one MAIN per (paper, user)" without blocking
    multiple NOTEs in the same scope.
    """
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "paper_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "parent_document_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(), nullable=False, server_default="note"),
        sa.Column("title", sa.String(), nullable=False, server_default="Untitled"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
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
    op.create_index(
        "ux_documents_main_per_paper",
        "documents",
        ["paper_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'main' AND paper_id IS NOT NULL"),
    )
    op.create_index(
        "ix_documents_paper_user", "documents", ["paper_id", "user_id"]
    )
    op.create_index(
        "ix_documents_parent", "documents", ["parent_document_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_documents_parent", table_name="documents")
    op.drop_index("ix_documents_paper_user", table_name="documents")
    op.drop_index("ux_documents_main_per_paper", table_name="documents")
    op.drop_table("documents")
