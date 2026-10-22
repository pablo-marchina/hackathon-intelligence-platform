"""Crawl-attributed canonical yield does not count references as new hackathons."""
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from hackathon_api.db import connection_dependency
from hackathon_api.main import app
from hackathon_api.repositories.canonical_yield import CanonicalYieldRepository
from hackathon_api.security import AdminPrincipal, admin_principal, require_admin
from hackathon_intelligence.incremental import CanonicalProcessor
from hackathon_sources.repositories import SourcePayloadRepository

NOW = datetime(2026, 10, 21, 16, tzinfo=UTC)


class Rows:
    def __init__(self, values):
        self.values = values

    def mappings(self):
        return self

    def all(self):
        return self.values


class DB:
    def __init__(self):
        self.queries = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append((sql, params))
        if 'FROM sources s' in sql:
            return Rows([
                dict(source_id='source-a', source_name='A', processed_versions=4,
                     created=1, linked=1, updated=1, rejected=1, unattributed=1),
                dict(source_id='source-b', source_name='B', processed_versions=2,
                     created=1, linked=1, updated=0, rejected=0, unattributed=0),
            ])
        if 'FROM canonical_processing_outcomes o' in sql:
            return Rows([dict(crawl_run_id='run-1', source_id='source-a',
                              processed_versions=3, created=1, linked=1, updated=0, rejected=1)])
        raise AssertionError(f'unexpected SQL: {sql[:120]}')


def test_real_canonical_yield_counts_versions_not_source_references():
    db = DB()
    report = CanonicalYieldRepository(db).snapshot(days=14, as_of=NOW)
    assert report['totals'] == dict(processed_versions=6, created=2, linked=2,
                                    updated=1, rejected=1, unattributed=1)
    assert report['runs'][0]['crawl_run_id'] == 'run-1'
    assert report['methodology']['linked'].startswith('Source record linked')
    assert db.queries[0][1]['since'] == NOW - timedelta(days=14)
    assert 'o.crawl_run_id IS NOT NULL' in db.queries[1][0]
    assert 'LIMIT 100' in db.queries[1][0]


@pytest.mark.parametrize('days', [0, 91])
def test_reject_invalid_window_before_query(days):
    db = DB()
    with pytest.raises(ValueError, match='days'):
        CanonicalYieldRepository(db).snapshot(days=days)
    assert not db.queries


def test_reject_naive_asof():
    with pytest.raises(ValueError, match='timezone-aware'):
        CanonicalYieldRepository(DB()).snapshot(as_of=datetime(2026, 10, 21))


def test_admin_endpoint_requires_auth_and_bounds_window():
    db = DB()
    with TestClient(app) as client:
        assert client.get('/admin/canonical-yield').status_code in (401, 503)
        app.dependency_overrides[require_admin] = lambda: None
        app.dependency_overrides[admin_principal] = lambda: AdminPrincipal(None, 'API_KEY', 'VIEWER')
        app.dependency_overrides[connection_dependency] = lambda: db
        try:
            result = client.get('/admin/canonical-yield?days=7')
            assert result.status_code == 200, result.text
            assert result.json()['totals']['created'] == 2
            assert client.get('/admin/canonical-yield?days=91').status_code == 422
        finally:
            app.dependency_overrides.clear()


def test_stage_attaches_original_crawl_but_does_not_reassign_unchanged_snapshot():
    conn = Mock()
    staged = SourcePayloadRepository(conn)
    staged.stage(ref_id='00000000-0000-4000-8000-000000000011',
                 source_id='00000000-0000-4000-8000-000000000022',
                 raw_document_id=None, source_key='authorized_source',
                 payload={'name': 'University Hackathon'},
                 crawl_run_id='00000000-0000-4000-8000-000000000033')
    sql = str(conn.execute.call_args.args[0]); params = conn.execute.call_args.args[1]
    assert 'staged_crawl_run_id=EXCLUDED.staged_crawl_run_id' in sql
    assert 'source_event_payloads.content_hash IS DISTINCT FROM EXCLUDED.content_hash' in sql
    assert params['crawl_run_id'] == '00000000-0000-4000-8000-000000000033'


@pytest.mark.parametrize('result,canonical_id', [
    ('CREATED', '00000000-0000-4000-8000-000000000044'),
    ('LINKED', '00000000-0000-4000-8000-000000000044'),
    ('UPDATED', '00000000-0000-4000-8000-000000000044'),
    ('REJECTED', None),
])
def test_outcome_is_append_only_keyed_by_record_and_version(result, canonical_id):
    processor = CanonicalProcessor(Mock())
    row = dict(ref_id='00000000-0000-4000-8000-000000000011',
               source_id='00000000-0000-4000-8000-000000000022',
               crawl_run_id=None, content_hash='a' * 64)
    processor._record_outcome(row, outcome=result, hackathon_id=canonical_id)
    sql = str(processor.connection.execute.call_args.args[0])
    params = processor.connection.execute.call_args.args[1]
    assert 'ON CONFLICT (source_event_ref_id, content_hash) DO NOTHING' in sql
    assert params['run'] is None
    assert params['outcome'] == result and params['event'] == canonical_id


def test_migration_is_current_and_outcomes_are_atomic():
    from pathlib import Path
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from hackathon_api.main import EXPECTED_DB_REVISION
    root = Path(__file__).resolve().parents[3]
    assert ScriptDirectory.from_config(Config(str(root / 'db/alembic.ini'))).get_current_head() == EXPECTED_DB_REVISION
    assert EXPECTED_DB_REVISION == '0013_canonical_yield'
    sql = (root / 'db/sql/0013_canonical_yield.sql').read_text()
    assert 'CREATE TABLE canonical_processing_outcomes' in sql
    assert 'UNIQUE (source_event_ref_id, content_hash)' in sql
    module = (root / 'packages/intelligence/src/hackathon_intelligence/incremental.py').read_text()
    assert module.index('self._mark(row)') < module.index('self._record_outcome(row, outcome=result')


def test_read_only_cli_displays_clear_sources_and_known_unknowns():
    from scripts.canonical_yield_report import render_markdown
    report = CanonicalYieldRepository(DB()).snapshot(as_of=NOW)
    markdown = render_markdown(report)
    assert '**2 created**' in markdown and '**2 linked**' in markdown
    assert 'not** events announced recently' in markdown
    assert 'source-a' in markdown
    assert 'A | 1 | 1 | 1 | 1 | 1' in markdown


def test_dashboard_uses_correct_coverage_insights_and_yield_slots():
    from pathlib import Path
    ui = (Path(__file__).resolve().parents[2] / 'web/components/AdminDashboard.tsx').read_text()
    a = ui.index("adminRequest<Coverage>('/coverage')")
    b = ui.index("adminRequest<SourceInsights>('/source-insights")
    c = ui.index("adminRequest<CanonicalYield>('/canonical-yield")
    assert a < b < c
    assert 'setCoverage(nextCoverage);setInsights(nextInsights);setCanonicalYield(nextYield)' in ui
    assert 'Verified canonical yield' in ui
