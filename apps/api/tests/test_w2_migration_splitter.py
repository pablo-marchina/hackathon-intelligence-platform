import importlib.util
from pathlib import Path


def _load_migration():
    root = Path(__file__).resolve().parents[3]
    path = root / "db/alembic/versions/0002_w2_entity_resolution_hardening.py"
    spec = importlib.util.spec_from_file_location("w2_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_w2_sql_contains_atomic_merge_undo_split_and_series_functions() -> None:
    root = Path(__file__).resolve().parents[3]
    sql = (root / "db/sql/0002_w2_entity_resolution_hardening.sql").read_text(encoding="utf-8")
    assert "merge_hackathons_transactional" in sql
    assert "undo_hackathon_merge" in sql
    assert "split_hackathon_merge" in sql
    assert "assign_hackathon_series" in sql
    assert "merge_source_ref_moves" in sql
    assert "merge_observation_moves" in sql
    assert "event_series_assignment_audit" in sql
    assert "FOR UPDATE" in sql


def test_w2_migration_splitter_keeps_plpgsql_bodies_intact() -> None:
    root = Path(__file__).resolve().parents[3]
    sql = (root / "db/sql/0002_w2_entity_resolution_hardening.sql").read_text(encoding="utf-8")
    migration = _load_migration()
    statements = migration._split_sql(sql)
    functions = [statement for statement in statements if "CREATE OR REPLACE FUNCTION" in statement]
    assert len(functions) == 5
    assert any("merge_hackathons_transactional" in statement and "RETURN v_merge_id" in statement for statement in functions)
    assert any("undo_hackathon_merge" in statement and "reversed_at = now()" in statement for statement in functions)
    assert any("split_hackathon_merge" in statement and "PERFORM undo_hackathon_merge" in statement for statement in functions)
    assert any("assign_hackathon_series" in statement and "event_series_assignment_audit" in statement for statement in functions)
