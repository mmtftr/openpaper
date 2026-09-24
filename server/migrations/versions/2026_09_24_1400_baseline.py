"""Baseline schema (history squashed 2026-09-24).

The earlier migrations were collapsed into this one after the personal-only
cleanup; the live database was stamped at this revision. Schema DDL lives in
`baseline.sql` next to this file (a `pg_dump --schema-only` of the migrated
database).

Revision ID: baseline_20260924
Revises:
Create Date: 2026-09-24 14:00:00
"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "baseline_20260924"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sql = (Path(__file__).with_name("baseline.sql")).read_text()
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    raise NotImplementedError("baseline")
