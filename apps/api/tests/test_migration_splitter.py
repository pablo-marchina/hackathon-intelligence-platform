from __future__ import annotations

import importlib.util
from pathlib import Path


def load_migration_module():
    path = Path(__file__).resolve().parents[3] / "db/alembic/versions/0001_initial_schema.py"
    spec = importlib.util.spec_from_file_location("migration_0001", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_splitter_preserves_plpgsql_dollar_body() -> None:
    module = load_migration_module()
    script = "CREATE FUNCTION x() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$; CREATE TABLE y(id int);"
    statements = module._split_sql(script)
    assert len(statements) == 2
    assert "RETURN NEW; END;" in statements[0]
    assert statements[1].startswith("CREATE TABLE y")
