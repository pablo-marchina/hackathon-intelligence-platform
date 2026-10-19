"""initial PostgreSQL physical schema v0.1

Revision ID: 0001_initial_schema
Revises: None
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "0001_initial_schema.sql"


def _split_sql(script: str) -> list[str]:
    """Split PostgreSQL statements while respecting quotes and dollar-quoted bodies."""
    statements: list[str] = []
    buffer: list[str] = []
    in_single = False
    in_double = False
    dollar_tag: str | None = None
    i = 0

    while i < len(script):
        char = script[i]

        if dollar_tag is not None:
            if script.startswith(dollar_tag, i):
                buffer.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buffer.append(char)
            i += 1
            continue

        if not in_single and not in_double and char == "$":
            end = script.find("$", i + 1)
            if end != -1:
                candidate = script[i : end + 1]
                inner = candidate[1:-1]
                if not inner or inner.replace("_", "a").isalnum():
                    dollar_tag = candidate
                    buffer.append(candidate)
                    i = end + 1
                    continue

        if char == "'" and not in_double:
            if in_single and i + 1 < len(script) and script[i + 1] == "'":
                buffer.extend(["'", "'"])
                i += 2
                continue
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double

        if char == ";" and not in_single and not in_double:
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer.clear()
        else:
            buffer.append(char)
        i += 1

    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def upgrade() -> None:
    bind = op.get_bind()
    script = SQL_PATH.read_text(encoding="utf-8")
    for statement in _split_sql(script):
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    statements = [
        "DROP TABLE IF EXISTS quality_snapshots CASCADE",
        "DROP TABLE IF EXISTS review_queue_items CASCADE",
        "DROP TABLE IF EXISTS admin_audit_log CASCADE",
        "DROP TABLE IF EXISTS alert_deliveries CASCADE",
        "DROP TABLE IF EXISTS alert_events CASCADE",
        "DROP TABLE IF EXISTS alert_rules CASCADE",
        "DROP TABLE IF EXISTS saved_searches CASCADE",
        "DROP TABLE IF EXISTS recommendation_scores CASCADE",
        "DROP TABLE IF EXISTS eligibility_evaluations CASCADE",
        "DROP TABLE IF EXISTS user_hackathon_states CASCADE",
        "DROP TABLE IF EXISTS user_preferred_technologies CASCADE",
        "DROP TABLE IF EXISTS user_preferred_tracks CASCADE",
        "DROP TABLE IF EXISTS user_preferences CASCADE",
        "DROP TABLE IF EXISTS user_identities CASCADE",
        "DROP TABLE IF EXISTS users CASCADE",
        "DROP TABLE IF EXISTS merge_audit CASCADE",
        "DROP TABLE IF EXISTS hackathon_aliases CASCADE",
        "DROP TABLE IF EXISTS dedup_candidates CASCADE",
        "DROP TABLE IF EXISTS canonical_changes CASCADE",
        "DROP TABLE IF EXISTS field_conflicts CASCADE",
        "DROP TABLE IF EXISTS canonical_field_selections CASCADE",
        "DROP TABLE IF EXISTS source_observations CASCADE",
        "DROP TABLE IF EXISTS extraction_runs CASCADE",
        "DROP TABLE IF EXISTS source_event_refs CASCADE",
        "DROP TABLE IF EXISTS raw_documents CASCADE",
        "DROP TABLE IF EXISTS fetch_attempts CASCADE",
        "DROP TABLE IF EXISTS url_frontier CASCADE",
        "DROP TABLE IF EXISTS crawl_runs CASCADE",
        "DROP TABLE IF EXISTS sources CASCADE",
        "DROP TABLE IF EXISTS awards CASCADE",
        "DROP TABLE IF EXISTS project_technologies CASCADE",
        "DROP TABLE IF EXISTS projects CASCADE",
        "DROP TABLE IF EXISTS participant_metric_observations CASCADE",
        "DROP TABLE IF EXISTS external_links CASCADE",
        "DROP TABLE IF EXISTS event_resources CASCADE",
        "DROP TABLE IF EXISTS requirements CASCADE",
        "DROP TABLE IF EXISTS judging_criteria CASCADE",
        "DROP TABLE IF EXISTS prizes CASCADE",
        "DROP TABLE IF EXISTS prize_pools CASCADE",
        "DROP TABLE IF EXISTS event_phases CASCADE",
        "DROP TABLE IF EXISTS eligibility_countries CASCADE",
        "DROP TABLE IF EXISTS eligibility_rules CASCADE",
        "DROP TABLE IF EXISTS hackathon_technologies CASCADE",
        "DROP TABLE IF EXISTS hackathon_tracks CASCADE",
        "DROP TABLE IF EXISTS event_people CASCADE",
        "DROP TABLE IF EXISTS hackathon_organizations CASCADE",
        "DROP TABLE IF EXISTS hackathon_locations CASCADE",
        "DROP TABLE IF EXISTS hackathons CASCADE",
        "DROP TABLE IF EXISTS taxonomy_aliases CASCADE",
        "DROP TABLE IF EXISTS technologies CASCADE",
        "DROP TABLE IF EXISTS tracks CASCADE",
        "DROP TABLE IF EXISTS people CASCADE",
        "DROP TABLE IF EXISTS organizations CASCADE",
        "DROP TABLE IF EXISTS locations CASCADE",
        "DROP TABLE IF EXISTS event_series CASCADE",
        "DROP FUNCTION IF EXISTS set_updated_at() CASCADE",
    ]
    enum_types = [
        "auth_provider", "frontier_state", "fetch_status", "crawl_run_status",
        "extraction_method", "phase_type", "resource_type", "requirement_type",
        "person_role", "organization_role", "alert_delivery_status", "alert_channel",
        "user_event_state", "eligibility_result", "dedup_decision", "conflict_severity",
        "review_state", "source_health", "source_ingestion_mode", "location_type",
        "event_format", "event_status", "event_type",
    ]
    for statement in statements:
        bind.exec_driver_sql(statement)
    for enum_type in enum_types:
        bind.exec_driver_sql(f"DROP TYPE IF EXISTS {enum_type}")
