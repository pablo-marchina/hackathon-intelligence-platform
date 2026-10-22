from pathlib import Path


def test_w3_migration_contains_discovery_persistence_and_admin_feedback() -> None:
    root = Path(__file__).resolve().parents[3]
    sql = (root / "db/sql/0003_w3_open_web_discovery.sql").read_text(encoding="utf-8")
    for table in (
        "discovery_query_templates",
        "discovery_runs",
        "discovery_queries",
        "discovery_results",
        "domain_discovery_budgets",
        "page_classifications",
        "discovery_classifier_feedback",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "evidence_authority = 'DISCOVERY_ONLY'" in sql
    assert "enqueue_discovery_false_positive_review" in sql
    assert "DISCOVERY_FALSE_POSITIVE" in sql
    assert "ingest_discovery_result_to_frontier" in sql


def test_w3_alembic_downgrade_is_reversible() -> None:
    root = Path(__file__).resolve().parents[3]
    migration = (root / "db/alembic/versions/0003_w3_open_web_discovery.py").read_text(
        encoding="utf-8"
    )
    assert 'down_revision: str | None = "0002_w2_entity_resolution_hardening"' in migration
    assert "DROP TRIGGER IF EXISTS discovery_false_positive_review_trg" in migration
    assert "DROP TABLE IF EXISTS discovery_query_templates" in migration
