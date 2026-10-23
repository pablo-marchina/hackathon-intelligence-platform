"""Curated text attributes for sourced eligibility and awards."""

from pathlib import Path

from alembic import op

revision = "0015_event_enrichment"
down_revision = "0014_typed_attribute_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (
        Path(__file__).resolve().parents[2] / "sql" / "0015_event_enrichment_attributes.sql"
    ).read_text(encoding="utf-8")
    for statement in sql.split(";"):
        statement = "\n".join(
            line for line in statement.splitlines() if not line.lstrip().startswith("--")
        )
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute(
        "DELETE FROM attribute_definitions "
        "WHERE key IN ('participation_requirements', 'prize_details_text')"
    )
