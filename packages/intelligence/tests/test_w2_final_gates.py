from pathlib import Path


def test_review_band_is_enqueued_by_database_trigger():
    sql = Path("db/sql/0002_w2_entity_resolution_hardening.sql").read_text()
    assert "enqueue_dedup_candidate_for_review" in sql
    assert "dedup_candidate_review_queue_trg" in sql
    assert "review_queue_items" in sql
    assert "NEW.decision = 'REVIEW'" in sql


def test_w2_sql_exposes_explicit_transactional_split_and_auditable_alias_undo():
    sql = Path("db/sql/0002_w2_entity_resolution_hardening.sql").read_text()
    assert "CREATE OR REPLACE FUNCTION split_hackathon_merge" in sql
    assert "PERFORM undo_hackathon_merge" in sql
    assert "alias_id uuid REFERENCES hackathon_aliases(id) ON DELETE SET NULL" in sql
    assert "alias_type text NOT NULL" in sql
    assert "alias_value text NOT NULL" in sql


def test_w2_downgrade_removes_review_trigger_and_functions():
    migration = Path("db/alembic/versions/0002_w2_entity_resolution_hardening.py").read_text()
    assert (
        "DROP TRIGGER IF EXISTS dedup_candidate_review_queue_trg ON dedup_candidates" in migration
    )
    assert "DROP FUNCTION IF EXISTS enqueue_dedup_candidate_for_review()" in migration
    assert "DROP FUNCTION IF EXISTS split_hackathon_merge(uuid,text,uuid)" in migration
    assert "DROP FUNCTION IF EXISTS undo_hackathon_merge(uuid,text,uuid)" in migration
