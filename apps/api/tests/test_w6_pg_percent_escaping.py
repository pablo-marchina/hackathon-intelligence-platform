"""Guard literal PostgreSQL PL/pgSQL percent tokens in psycopg migrations."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("name", [
    "0002_w2_entity_resolution_hardening", "0003_w3_open_web_discovery"
])
def test_literal_percent_signs_escaped_before_driver(name):
    migration = (ROOT / "db" / "alembic" / "versions" / f"{name}.py").read_text()
    sql = (ROOT / "db" / "sql" / f"{name}.sql").read_text()
    assert "%ROWTYPE" in sql
    assert 'bind.exec_driver_sql(statement.replace("%", "%%"))' in migration
    assert "%%ROWTYPE" in sql.replace("%", "%%")


def test_alembic_revision_column_widened_before_long_w2_revision():
    migration = (ROOT / "db" / "alembic" / "versions" / "0002_w2_entity_resolution_hardening.py").read_text()
    assert "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)" in migration
    assert migration.index("ALTER TABLE alembic_version") < migration.index("for statement in _split_sql")
