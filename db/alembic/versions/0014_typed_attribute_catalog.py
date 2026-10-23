"""Typed attribute registry over existing field-level canonical evidence."""

from pathlib import Path

from alembic import op

revision = "0014_typed_attribute_catalog"
down_revision = "0013_canonical_yield"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (
        Path(__file__).resolve().parents[2] / "sql" / "0014_typed_attribute_catalog.sql"
    ).read_text(encoding="utf-8")
    for statement in sql.split(";"):
        statement = "\n".join(
            line for line in statement.splitlines() if not line.lstrip().startswith("--")
        )
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE attribute_definitions")
