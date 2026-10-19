"""W6 durable saved-search match watermark.

Revision ID: 0009_w6_alert_match
Revises: 0008_w6_alert_dispatch
"""
from __future__ import annotations

from pathlib import Path
from alembic import op

revision = "0009_w6_alert_match"
down_revision = "0008_w6_alert_dispatch"
branch_labels = None
depends_on = None
SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "0009_w6_alert_match.sql"


def upgrade() -> None:
    sql = SQL_PATH.read_text(encoding="utf-8")
    without_comments = "\n".join(line for line in sql.splitlines()
                                 if not line.lstrip().startswith("--"))
    for statement in without_comments.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS hackathons_match_scan_idx")
    op.execute("DROP TABLE IF EXISTS alert_match_progress")
