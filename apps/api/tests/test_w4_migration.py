from pathlib import Path


def test_w4_migration_contains_search_auth_and_privacy_boundaries():
    root = Path(__file__).resolve().parents[3]
    sql = (root / "db/sql/0004_w4_api_search_frontend.sql").read_text()
    assert "hackathon_search_documents" in sql
    assert "rebuild_hackathon_search_documents" in sql
    assert "auth_sessions" in sql
    assert "privacy_requests" in sql


def test_w4_alembic_downgrade_is_reversible():
    root = Path(__file__).resolve().parents[3]
    m = (root / "db/alembic/versions/0004_w4_api_search_frontend.py").read_text()
    assert "0003_w3_open_web_discovery" in m
    assert "DROP TABLE IF EXISTS auth_sessions" in m
    assert "DROP TABLE IF EXISTS hackathon_search_documents" in m
