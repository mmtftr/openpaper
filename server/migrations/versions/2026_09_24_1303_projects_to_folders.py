"""projects become single-owner folders

Drops the multi-user and project-artifact machinery: data tables, project
roles/invitations, paper sharing/forking columns, user blocking, the
non-paper conversations, and the second (paper-less) user account.
Renames project.admin_id to owner_id.

Revision ID: 5cec2d370cbe
Revises: e0f1a2b3c4d5
Create Date: 2026-09-24 13:03:00.000000+00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5cec2d370cbe"
down_revision: Union[str, None] = "e0f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# admin@perf.local — a second account with no papers, conversations, notes,
# highlights, annotations or projects (only a subscription row).
SECOND_USER_ID = "c983d1c1-592e-48f1-9e0b-d3c657e166cf"

# Tables whose user FK does not cascade, in child -> parent order. Checked
# for existence because sibling migrations of this refactor drop some of them.
_SECOND_USER_ROWS = [
    ("messages", "user_id"),
    ("annotations", "user_id"),
    ("highlights", "user_id"),
    ("paper_notes", "user_id"),
    ("conversations", "user_id"),
    ("project", "admin_id"),
    ("papers", "user_id"),
    # These cascade from users, but are deleted explicitly for clarity.
    ("sessions", "user_id"),
    ("subscriptions", "user_id"),
    ("onboarding", "user_id"),
    ("paper_tags", "user_id"),
    ("paper_upload_jobs", "user_id"),
    ("audio_overview_jobs", "user_id"),
    ("audio_overviews", "user_id"),
    ("discover_searches", "user_id"),
    ("documents", "user_id"),
    ("chat_usage_events", "user_id"),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Data tables (0 rows): rows -> results -> jobs.
    op.drop_table("data_table_rows")
    op.drop_table("data_table_extraction_results")
    op.drop_table("data_table_extraction_jobs")

    # Collaborators / invitations (0 rows).
    op.drop_table("project_role_invitations")
    op.drop_table("project_role")

    # Non-paper conversations: 2 orphan 'everything' conversations (4
    # messages); no 'project' ones exist.
    op.execute(
        "DELETE FROM messages WHERE conversation_id IN "
        "(SELECT id FROM conversations WHERE conversable_type <> 'paper')"
    )
    op.execute("DELETE FROM conversations WHERE conversable_type <> 'paper'")
    op.drop_constraint("check_conversable_id_paper", "conversations", type_="check")
    op.create_check_constraint(
        "check_conversable_paper",
        "conversations",
        "conversable_type = 'paper' AND conversable_id IS NOT NULL",
    )

    # Sharing / forking (no paper is public, shared or forked).
    op.drop_index("ix_papers_share_id", table_name="papers")
    op.drop_column("papers", "share_id")
    op.drop_column("papers", "is_public")
    op.drop_constraint("papers_parent_paper_id_fkey", "papers", type_="foreignkey")
    op.drop_column("papers", "parent_paper_id")

    # Admin block.
    op.drop_column("users", "is_blocked")

    # The second user (only its subscription row exists).
    for table, column in _SECOND_USER_ROWS:
        if inspector.has_table(table):
            op.execute(
                sa.text(f"DELETE FROM {table} WHERE {column} = :uid").bindparams(
                    uid=SECOND_USER_ID
                )
            )
    op.execute(
        sa.text("DELETE FROM users WHERE id = :uid").bindparams(uid=SECOND_USER_ID)
    )

    # Projects are single-owner folders.
    op.alter_column("project", "admin_id", new_column_name="owner_id")
    op.execute(
        "ALTER TABLE project RENAME CONSTRAINT project_admin_id_fkey "
        "TO project_owner_id_fkey"
    )


def downgrade() -> None:
    raise NotImplementedError("projects_to_folders is a one-way migration")
