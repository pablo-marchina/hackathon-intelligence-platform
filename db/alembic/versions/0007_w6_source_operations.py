"""W6 durable adaptive scheduler and deduplicated regression notices.

Revision ID: 0007_w6_source_operations
Revises: 0006_w6_admin_control_plane
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0007_w6_source_operations"
down_revision: str | None = "0006_w6_admin_control_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "0007_w6_source_operations.sql"


def upgrade() -> None:
    # The SQL file contains plain DDL with no stored routines/embedded semicolons.
    for statement in SQL_PATH.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS source_regression_state")
    op.execute("DROP TABLE IF EXISTS source_operational_state")
