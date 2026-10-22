from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PATHS = [
    "apps/api/src/hackathon_api/main.py",
    "apps/worker/src/hackathon_worker/app.py",
    "apps/web/package.json",
    "packages/shared/src/hackathon_shared/settings.py",
    "infra/docker/compose.yml",
    "db/alembic/versions/0001_initial_schema.py",
    "db/alembic/versions/0002_w2_entity_resolution_hardening.py",
    "db/alembic/versions/0003_w3_open_web_discovery.py",
    "db/alembic/versions/0004_w4_api_search_frontend.py",
    "db/alembic/versions/0005_w5_recommendation_alerts.py",
    "db/alembic/versions/0006_w6_admin_control_plane.py",
    "db/alembic/versions/0007_w6_source_operations.py",
    "db/sql/0001_initial_schema.sql",
    "db/sql/0002_w2_entity_resolution_hardening.sql",
    "db/sql/0003_w3_open_web_discovery.sql",
    "db/sql/0004_w4_api_search_frontend.sql",
    "db/sql/0005_w5_recommendation_alerts.sql",
    "db/sql/0006_w6_admin_control_plane.sql",
    "db/sql/0007_w6_source_operations.sql",
    "evals/gates/w6_baseline_v1.json",
    ".github/workflows/ci.yml",
]

EXPECTED_TABLES = {
    "hackathons",
    "event_series",
    "locations",
    "organizations",
    "people",
    "tracks",
    "technologies",
    "eligibility_rules",
    "event_phases",
    "prize_pools",
    "prizes",
    "sources",
    "crawl_runs",
    "url_frontier",
    "fetch_attempts",
    "raw_documents",
    "source_observations",
    "canonical_field_selections",
    "field_conflicts",
    "canonical_changes",
    "dedup_candidates",
    "users",
    "saved_searches",
    "alert_events",
    "review_queue_items",
    "quality_snapshots",
    "event_series_assignment_audit",
    "merge_source_ref_moves",
    "merge_observation_moves",
    "merge_alias_changes",
    "discovery_query_templates",
    "discovery_runs",
    "discovery_queries",
    "discovery_results",
    "domain_discovery_budgets",
    "page_classifications",
    "discovery_classifier_feedback",
    "hackathon_search_documents",
    "auth_sessions",
    "privacy_requests",
    "source_operational_state",
    "source_regression_state",
    "user_preference_versions",
    "alert_channel_preferences",
    "admin_user_roles",
}
EXPECTED_EXTENSIONS = {"pgcrypto", "citext", "pg_trgm", "unaccent", "vector"}


def main() -> None:
    missing = [path for path in REQUIRED_PATHS if not (ROOT / path).exists()]
    if missing:
        raise SystemExit(f"Missing required paths: {missing}")

    sql_files = sorted((ROOT / "db/sql").glob("*.sql"))
    sql = "\n".join(path.read_text(encoding="utf-8") for path in sql_files)
    tables = set(
        re.findall(r"CREATE TABLE(?: IF NOT EXISTS)?\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.I)
    )
    extensions = set(re.findall(r"CREATE EXTENSION IF NOT EXISTS\s+([a-zA-Z0-9_]+)", sql, re.I))

    missing_tables = EXPECTED_TABLES - tables
    missing_extensions = EXPECTED_EXTENSIONS - extensions
    if missing_tables or missing_extensions:
        raise SystemExit(
            f"Schema verification failed. Missing tables={sorted(missing_tables)} "
            f"extensions={sorted(missing_extensions)}"
        )

    if "vector(384)" not in sql:
        raise SystemExit("Expected 384-dimensional vector field missing")
    if "canonical_field_one_active_idx" not in sql:
        raise SystemExit("Canonical field active-selection constraint missing")
    if "dedup_candidate_pair_unique_idx" not in sql:
        raise SystemExit("Order-independent dedup unique index missing")
    for function_name in (
        "merge_hackathons_transactional",
        "undo_hackathon_merge",
        "split_hackathon_merge",
        "assign_hackathon_series",
    ):
        if function_name not in sql:
            raise SystemExit(f"Expected W2 function missing: {function_name}")

    for function_name in (
        "ingest_discovery_result_to_frontier",
        "enqueue_discovery_false_positive_review",
    ):
        if function_name not in sql:
            raise SystemExit(f"Expected W3 function missing: {function_name}")
    if "DISCOVERY_ONLY" not in sql or "DISCOVERY_FALSE_POSITIVE" not in sql:
        raise SystemExit("W3 provenance/admin safeguards missing")

    for function_name in (
        "refresh_hackathon_search_document",
        "rebuild_hackathon_search_documents",
    ):
        if function_name not in sql:
            raise SystemExit(f"Expected W4 search function missing: {function_name}")
    if "auth_sessions" not in sql or "privacy_requests" not in sql:
        raise SystemExit("W4 auth/privacy schema safeguards missing")

    for token in (
        "user_preference_versions",
        "snapshot_hash",
        "input_hash",
        "definition_hash",
        "alert_channel_preferences",
        "next_attempt_at",
        "max_attempts",
    ):
        if token not in sql:
            raise SystemExit(f"Expected W5 reproducibility/delivery guard missing: {token}")

    print(f"repository verification OK: {len(tables)} SQL tables, {len(extensions)} extensions")


if __name__ == "__main__":
    main()
