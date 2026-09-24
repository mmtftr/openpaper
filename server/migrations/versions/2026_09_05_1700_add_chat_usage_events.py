"""add chat_usage_events for quota accounting of ephemeral model calls

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-09-05 17:00:00.000000+00:00

Chat credits are metered from `messages.content` length. Endpoints that call
a paid model WITHOUT persisting a message (the code-viewer quick question)
record one row here per exchange so the weekly meter still advances.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_usage_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_chat_usage_events_user_id_created_at",
        "chat_usage_events",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_usage_events_user_id_created_at", table_name="chat_usage_events"
    )
    op.drop_table("chat_usage_events")
