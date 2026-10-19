"""W6 alert outbox dispatch claims.

Revision ID: 0008_w6_alert_dispatch
Revises: 0007_w6_source_operations
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0008_w6_alert_dispatch"
down_revision = "0007_w6_source_operations"
branch_labels = None
depends_on = None
SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "0008_w6_alert_dispatch.sql"


def upgrade() -> None:
    for statement in SQL_PATH.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS alert_delivery_due_idx")
    op.execute("ALTER TABLE alert_deliveries DROP COLUMN IF EXISTS dispatch_expires_at, DROP COLUMN IF EXISTS dispatch_token")
