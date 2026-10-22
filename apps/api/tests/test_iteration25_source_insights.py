"""Iteration 25: read-only source prioritization with honest cohort boundaries."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hackathon_api.db import connection_dependency
from hackathon_api.main import app
from hackathon_api.repositories.source_insights import SourceInsightsRepository
from hackathon_api.security import AdminPrincipal, admin_principal, require_admin

NOW = datetime(2026, 10, 21, 13, tzinfo=UTC)


class Result:
    def __init__(self, items):
        self.items = items

    def mappings(self):
        return self

    def all(self):
        return self.items


class FakeConnection:
    def __init__(self, *, empty=False):
        self.empty = empty
        self.queries = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append((sql, params))
        if self.empty:
            return Result([])
        if 'FROM sources s' in sql:
            return Result([
                dict(source_id='one', name='Approved source', active=True, permission_status='ALLOWED',
                     discovery_enabled=True, detail_extraction_enabled=True,
                     ingestion_mode='RSS', priority=60, new_refs=12, ready_urls=5, exhausted_urls=2),
                dict(source_id='two', name='Restricted source', active=True, permission_status='REVIEW_REQUIRED',
                     discovery_enabled=True, detail_extraction_enabled=True,
                     ingestion_mode='SITEMAP', priority=100, new_refs=80, ready_urls=200, exhausted_urls=0),
                dict(source_id='three', name='No runs yet', active=True, permission_status='ALLOWED',
                     discovery_enabled=True, detail_extraction_enabled=True,
                     ingestion_mode='API', priority=50, new_refs=0, ready_urls=10, exhausted_urls=0),
            ])
        if 'FROM crawl_runs' in sql and 'GROUP BY source_id' in sql:
            return Result([
                dict(source_id='one', run_count=4, succeeded=3, partial=0, failed=1,
                     cancelled=0, running=0, fetched_pages=20, parsed_records=15, reported_failures=1),
                dict(source_id='two', run_count=1, succeeded=1, partial=0, failed=0,
                     cancelled=0, running=0, fetched_pages=5, parsed_records=4, reported_failures=0),
            ])
        if 'FROM classes' in sql:
            return Result([
                dict(source_id='one', reason='NOT_HACKATHON_OR_MISSING_NAME', count=4),
                dict(source_id='one', reason='PROCESSING_ERROR_PENDING', count=1),
                dict(source_id='two', reason='OTHER_PROCESSED_REJECTION', count=55),
            ])
        if 'FROM crawl_runs' in sql and 'LIMIT :limit' in sql:
            return Result([
                dict(id='run', source_id='one', status='SUCCEEDED',
                     started_at=NOW - timedelta(minutes=30), finished_at=NOW - timedelta(minutes=29),
                     discovered_count=10, fetched_count=7, parsed_count=5, failed_count=0,
                     valid_event_count=5, unique_event_count=5),
            ])
        raise AssertionError(f'unexpected SQL {sql[:120]}')


def test_prioritization_never_automatically_approves_sources():
    report = SourceInsightsRepository(FakeConnection()).snapshot(as_of=NOW)
    eligible = report['sources'][0]
    assert eligible['name'] == 'Approved source'
    assert eligible['triage_score'] is not None
    assert eligible['suggested_action'] == 'INVESTIGATE_FAILURES'
    assert eligible['runs_in_window']['run_count'] == 4
    assert eligible['new_references_in_window'] == 12
    restricted = next(s for s in report['sources'] if s['source_id'] == 'two')
    assert restricted['triage_score'] is None
    assert restricted['suggested_action'] == 'REVIEW_PERMISSIONS_OR_PAUSE'
    no_runs = next(s for s in report['sources'] if s['source_id'] == 'three')
    assert no_runs['triage_score'] is None
    assert no_runs['suggested_action'] == 'VALIDATE_WITH_CONTROLLED_RUN'
    assert report['methodology']['historical_runs'].startswith('Runs in the rolling')


def test_current_rejections_are_redacted_not_raw_exception_details():
    db = FakeConnection()
    report = SourceInsightsRepository(db).snapshot(as_of=NOW)
    assert report['sources'][0]['current_rejection_reasons'] == {
        'NOT_HACKATHON_OR_MISSING_NAME': 4, 'PROCESSING_ERROR_PENDING': 1,
    }
    sql = next(s for s, _ in db.queries if 'FROM classes' in s)
    assert 'last_error = ' in sql
    assert 'GROUP BY source_id, reason' in sql
    assert 'last_error AS' not in sql
    assert all('last_error' not in item for item in report['sources'])
    assert report['recent_runs'][0]['started_at'].endswith('+00:00')
    assert report['recent_runs'][0]['finished_at'].endswith('+00:00')


def test_unlimited_history_aggregates_and_bounded_detail_list():
    db = FakeConnection()
    report = SourceInsightsRepository(db).snapshot(as_of=NOW, recent_days=30, run_limit=2)
    assert report['sources'][0]['runs_in_window']['run_count'] == 4
    assert len(report['recent_runs']) == 1
    assert 'LIMIT :limit' in db.queries[-1][0]
    assert db.queries[-1][1]['limit'] == 2
    assert db.queries[0][1]['since'] == NOW - timedelta(days=30)


def test_empty_database_does_not_invent_results():
    report = SourceInsightsRepository(FakeConnection(empty=True)).snapshot(as_of=NOW)
    assert report['sources'] == []
    assert report['recent_runs'] == []


def test_parameters_are_validated_before_querying():
    db = FakeConnection()
    with pytest.raises(ValueError, match='recent_days'):
        SourceInsightsRepository(db).snapshot(recent_days=0)
    with pytest.raises(ValueError, match='run_limit'):
        SourceInsightsRepository(db).snapshot(run_limit=201)
    with pytest.raises(ValueError, match='timezone-aware'):
        SourceInsightsRepository(db).snapshot(as_of=datetime(2026, 10, 21))
    assert db.queries == []


def test_admin_insights_endpoint_requires_admin_identity():
    db = FakeConnection()
    with TestClient(app) as client:
        assert client.get('/admin/source-insights').status_code in (401, 503)
        app.dependency_overrides[require_admin] = lambda: None
        app.dependency_overrides[admin_principal] = lambda: AdminPrincipal(None, 'API_KEY', 'VIEWER')
        app.dependency_overrides[connection_dependency] = lambda: db
        try:
            r = client.get('/admin/source-insights?recent_days=7&run_limit=3')
            assert r.status_code == 200, r.text
            assert r.json()['sources'][0]['eligible_for_controlled_collection'] is True
            assert client.get('/admin/source-insights?run_limit=500').status_code == 422
            assert client.get('/admin/source-insights?recent_days=0').status_code == 422
        finally:
            app.dependency_overrides.clear()


def test_browser_renders_insights_without_exposing_raw_errors():
    tsx = (Path(__file__).resolve().parents[2] / 'web/components/AdminDashboard.tsx').read_text()
    assert '/source-insights' in tsx
    assert 'Historical crawl health' in tsx
    assert 'current_rejection_reasons' in tsx
    assert 'triage_score' in tsx


def test_cli_is_read_only_and_reports_safe_aggregates():
    from scripts.source_insights_report import render_markdown
    report = SourceInsightsRepository(FakeConnection()).snapshot(as_of=NOW)
    markdown = render_markdown(report)
    assert 'Historical' not in markdown or 'historical' in markdown
    assert 'Approved source' in markdown
    assert 'Restricted source' in markdown
    assert 'Most recent crawl executions' in markdown
    assert 'Triage scores are advisory only' in markdown
    assert 'not_hackathon_like_or_missing_name' not in markdown
    script = (Path(__file__).resolve().parents[3] / 'scripts/ops/catalog_coverage_integration_check.py').read_text()
    assert 'SourceInsightsRepository(connection).snapshot' in script
    assert 'transaction.rollback()' in script
