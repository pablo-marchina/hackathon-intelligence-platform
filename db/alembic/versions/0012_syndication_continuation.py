"""Durable feed/sitemap continuation without silently losing entries."""

from pathlib import Path

from alembic import op

revision = "0012_syndication_continuation"
down_revision = "0011_source_url_backlog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (
        Path(__file__).resolve().parents[2] / "sql" / "0012_syndication_continuation.sql"
    ).read_text(encoding="utf-8")
    for statement in sql.split(";"):
        statement = "\n".join(
            line for line in statement.splitlines() if not line.lstrip().startswith("--")
        )
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE source_url_backlog DROP COLUMN listing_hash, DROP COLUMN listing_offset"
    )
