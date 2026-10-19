"""W4 API/search/auth foundation
Revision ID: 0004_w4_api_search_frontend
Revises: 0003_w3_open_web_discovery
"""
from __future__ import annotations
from pathlib import Path
from typing import Sequence
from alembic import op
revision: str = "0004_w4_api_search_frontend"
down_revision: str | None = "0003_w3_open_web_discovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SQL_PATH=Path(__file__).resolve().parents[2]/"sql"/"0004_w4_api_search_frontend.sql"
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
            statement=''.join(buffer).strip();
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
    statements=[
      "DROP TABLE IF EXISTS privacy_requests CASCADE","DROP TABLE IF EXISTS auth_sessions CASCADE",
      "DROP TRIGGER IF EXISTS w4_hackathon_tech_search_refresh_trg ON hackathon_technologies",
      "DROP TRIGGER IF EXISTS w4_hackathon_track_search_refresh_trg ON hackathon_tracks",
      "DROP TRIGGER IF EXISTS w4_hackathon_org_search_refresh_trg ON hackathon_organizations",
      "DROP TRIGGER IF EXISTS w4_hackathon_search_refresh_trg ON hackathons",
      "DROP FUNCTION IF EXISTS refresh_search_for_hackathon_row_trigger()","DROP FUNCTION IF EXISTS refresh_search_for_hackathon_trigger()",
      "DROP FUNCTION IF EXISTS rebuild_hackathon_search_documents()","DROP FUNCTION IF EXISTS refresh_hackathon_search_document(uuid)",
      "DROP TABLE IF EXISTS hackathon_search_documents CASCADE"]
    for statement in statements: bind.exec_driver_sql(statement)
