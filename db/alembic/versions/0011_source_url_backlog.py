"""Source-scoped durable feed URL queue.

Revision ID: 0011_source_url_backlog
Revises: 0010_incremental_canonical_pipeline
"""
from pathlib import Path
from alembic import op

revision = "0011_source_url_backlog"
down_revision = "0010_incremental_canonical_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (Path(__file__).resolve().parents[2] / "sql" / "0011_source_url_backlog.sql").read_text(encoding="utf-8")
    for stmt in sql.split(";"):
        if stmt.strip():
            op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS source_url_backlog")
