"""W2 entity resolution hardening

Revision ID: 0002_w2_entity_resolution_hardening
Revises: 0001_initial_schema
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from alembic import op

revision: str = "0002_w2_entity_resolution_hardening"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "0002_w2_entity_resolution_hardening.sql"


def _split_sql(script: str) -> list[str]:
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
    for statement in _split_sql(SQL_PATH.read_text(encoding="utf-8")):
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    statements = [
        "DROP TRIGGER IF EXISTS dedup_candidate_review_queue_trg ON dedup_candidates",
        "DROP FUNCTION IF EXISTS enqueue_dedup_candidate_for_review()",
        "DROP FUNCTION IF EXISTS split_hackathon_merge(uuid,text,uuid)",
        "DROP FUNCTION IF EXISTS undo_hackathon_merge(uuid,text,uuid)",
        "DROP FUNCTION IF EXISTS merge_hackathons_transactional(uuid,uuid,text,text,text,uuid)",
        "DROP FUNCTION IF EXISTS assign_hackathon_series(uuid,uuid,smallint,text,text,numeric,jsonb,text,uuid,text)",
        "DROP TABLE IF EXISTS merge_alias_changes CASCADE",
        "DROP TABLE IF EXISTS merge_observation_moves CASCADE",
        "DROP TABLE IF EXISTS merge_source_ref_moves CASCADE",
        "ALTER TABLE hackathon_aliases DROP COLUMN IF EXISTS created_by_merge_audit_id",
        "ALTER TABLE merge_audit DROP COLUMN IF EXISTS reversed_by_user_id",
        "DROP TABLE IF EXISTS event_series_assignment_audit CASCADE",
    ]
    for statement in statements:
        bind.exec_driver_sql(statement)
