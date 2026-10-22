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
