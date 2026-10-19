"""W5 recommendation and alerts hardening.
Revision ID: 0005_w5_recommendation_alerts
Revises: 0004_w4_api_search_frontend
"""
from __future__ import annotations
from pathlib import Path
from typing import Sequence
from alembic import op
revision: str = "0005_w5_recommendation_alerts"
down_revision: str | None = "0004_w4_api_search_frontend"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SQL_PATH=Path(__file__).resolve().parents[2]/"sql"/"0005_w5_recommendation_alerts.sql"
def _split_sql(script:str)->list[str]:
    statements=[]; buffer=[]; in_single=False; in_double=False; dollar_tag=None; i=0
    while i<len(script):
        char=script[i]
        if dollar_tag is not None:
            if script.startswith(dollar_tag,i): buffer.append(dollar_tag); i+=len(dollar_tag); dollar_tag=None; continue
            buffer.append(char); i+=1; continue
        if not in_single and not in_double and char=='$':
            end=script.find('$',i+1)
            if end!=-1:
                candidate=script[i:end+1]; inner=candidate[1:-1]
                if not inner or inner.replace('_','a').isalnum(): dollar_tag=candidate; buffer.append(candidate); i=end+1; continue
        if char=="'" and not in_double:
            if in_single and i+1<len(script) and script[i+1]=="'": buffer.extend(["'","'"]); i+=2; continue
            in_single=not in_single
        elif char=='"' and not in_single: in_double=not in_double
        if char==';' and not in_single and not in_double:
            statement=''.join(buffer).strip()
            if statement: statements.append(statement)
            buffer.clear()
        else: buffer.append(char)
        i+=1
    tail=''.join(buffer).strip()
    if tail: statements.append(tail)
    return statements

def upgrade()->None:
    bind=op.get_bind()
    for statement in _split_sql(SQL_PATH.read_text(encoding='utf-8')): bind.exec_driver_sql(statement)

def downgrade()->None:
    bind=op.get_bind()
    for statement in [
      "DROP INDEX IF EXISTS alert_delivery_retry_idx",
      "ALTER TABLE alert_deliveries DROP COLUMN IF EXISTS max_attempts, DROP COLUMN IF EXISTS next_attempt_at, DROP COLUMN IF EXISTS provider_response",
      "DROP TABLE IF EXISTS alert_channel_preferences",
      "DROP INDEX IF EXISTS saved_searches_definition_hash_idx",
      "ALTER TABLE saved_searches DROP COLUMN IF EXISTS definition_hash",
      "DROP INDEX IF EXISTS recommendation_input_uniq",
      "ALTER TABLE recommendation_scores DROP COLUMN IF EXISTS components, DROP COLUMN IF EXISTS event_snapshot_hash, DROP COLUMN IF EXISTS profile_version, DROP COLUMN IF EXISTS input_hash",
      "DROP TABLE IF EXISTS user_preference_versions",
      "ALTER TABLE user_preferences DROP COLUMN IF EXISTS snapshot_hash, DROP COLUMN IF EXISTS version",
    ]: bind.exec_driver_sql(statement)
