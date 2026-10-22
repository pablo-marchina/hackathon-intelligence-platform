"""W6 admin RBAC and durable audit identity.

Revision ID: 0006_w6_admin_control_plane
Revises: 0005_w5_recommendation_alerts
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0006_w6_admin_control_plane"
down_revision: str | None = "0005_w5_recommendation_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "0006_w6_admin_control_plane.sql"


def upgrade() -> None:
    # All statements in this migration are plain SQL (no semicolons inside strings/functions).
    for statement in SQL_PATH.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS admin_audit_created_at_idx")
    op.execute(
        "ALTER TABLE admin_audit_log DROP COLUMN IF EXISTS request_id, DROP COLUMN IF EXISTS actor_kind"
    )
    op.execute("DROP TABLE IF EXISTS admin_user_roles")
