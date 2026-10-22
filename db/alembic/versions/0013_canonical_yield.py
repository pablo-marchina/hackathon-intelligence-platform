"""Auditable crawl-attributed canonical processing outcomes."""
from pathlib import Path
from alembic import op

revision = "0013_canonical_yield"
down_revision = "0012_syndication_continuation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (Path(__file__).resolve().parents[2] / "sql" / "0013_canonical_yield.sql").read_text(encoding="utf-8")
    for statement in sql.split(";"):
        statement = "\n".join(line for line in statement.splitlines()
                              if not line.lstrip().startswith("--"))
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE canonical_processing_outcomes")
    op.execute("ALTER TABLE source_event_payloads DROP COLUMN staged_crawl_run_id")
