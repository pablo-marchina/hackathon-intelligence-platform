"""Durable, idempotent handoff from authorized source adapters to catalog.

Revision ID: 0010_incremental_canonical_pipeline
Revises: 0009_w6_alert_match
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0010_incremental_canonical_pipeline"
down_revision = "0009_w6_alert_match"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (
        Path(__file__).resolve().parents[2] / "sql" / "0010_incremental_canonical_pipeline.sql"
    ).read_text(encoding="utf-8")
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS source_event_payloads")
