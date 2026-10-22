"""W3 open-web discovery persistence

Revision ID: 0003_w3_open_web_discovery
Revises: 0002_w2_entity_resolution_hardening
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from alembic import op

revision: str = "0003_w3_open_web_discovery"
down_revision: str | None = "0002_w2_entity_resolution_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "0003_w3_open_web_discovery.sql"


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
        # psycopg uses %-style placeholders even for DDL: escape literal
        # PostgreSQL PL/pgSQL %ROWTYPE and RAISE format markers.
        bind.exec_driver_sql(statement.replace("%", "%%"))


def downgrade() -> None:
    bind = op.get_bind()
    statements = [
        "DROP FUNCTION IF EXISTS ingest_discovery_result_to_frontier(uuid,uuid,text,text,text,citext,integer,smallint,jsonb)",
        "DROP TRIGGER IF EXISTS discovery_false_positive_review_trg ON discovery_classifier_feedback",
        "DROP FUNCTION IF EXISTS enqueue_discovery_false_positive_review()",
        "DROP TABLE IF EXISTS discovery_classifier_feedback CASCADE",
        "DROP TABLE IF EXISTS page_classifications CASCADE",
        "DROP TABLE IF EXISTS domain_discovery_budgets CASCADE",
        "DROP TABLE IF EXISTS discovery_results CASCADE",
        "DROP TABLE IF EXISTS discovery_queries CASCADE",
        "DROP TABLE IF EXISTS discovery_runs CASCADE",
        "DROP TABLE IF EXISTS discovery_query_templates CASCADE",
    ]
    for statement in statements:
        bind.exec_driver_sql(statement)
