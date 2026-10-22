"""Live funnel accounting: keep URL tasks, source references, and canonicals separate."""
from datetime import UTC, datetime
from pathlib import Path

from hackathon_api.repositories.coverage import CoverageRepository
from scripts.catalog_coverage_report import render_markdown
from test_iteration17_coverage import FakeConnection

NOW = datetime(2026, 10, 21, 13, tzinfo=UTC)


def test_live_funnel_distinguishes_units_and_cross_source_duplicates():
    snapshot = CoverageRepository(FakeConnection()).snapshot(as_of=NOW)
    funnel = snapshot['conversion_funnel']
    assert (funnel['discovered_url_tasks'], funnel['fetched_discovered_url_tasks']) == (12, 8)
    assert funnel['fetch_percent'] == 66.7
    assert (funnel['active_references'], funnel['staged_references']) == (13, 5)
    assert (funnel['accepted_references'], funnel['rejected_references'], funnel['pending_references']) == (3, 1, 1)
    assert funnel['failed_pending_references'] == 1
    assert funnel['linked_searchable_references'] == 3
    assert funnel['unique_searchable_hackathons'] == 2
    assert funnel['staging_percent'] == 38.5
    assert funnel['acceptance_percent'] == 60.0
    assert funnel['linkage_percent'] == 100.0
    assert funnel['distinct_hackathons_percent'] == 66.7
    assert snapshot['catalog']['unique_active'] == 3  # historic backfills not part of pipeline cohort
    assert sum(s['funnel']['unique_searchable_hackathons'] for s in snapshot['sources']) == 3
    assert funnel['unique_searchable_hackathons'] == 2  # global DISTINCT, not per-source sum


def test_per_source_funnel_keeps_legacy_refs_and_only_counts_discovered_urls():
    snap = CoverageRepository(FakeConnection()).snapshot(as_of=NOW)
    a, b, c = snap['sources']
    assert a['funnel']['active_references'] == 8
    assert a['funnel']['staged_references'] == 3
    assert a['funnel']['staging_percent'] == 37.5
    assert a['discovered_urls'] == 12
    assert a['fetched_discovered_urls'] == 8
    assert a['discovery_fetch_percent'] == 66.7
    assert b['funnel']['failed_pending_references'] == 1
    assert b['discovery_fetch_percent'] is None  # no task cohort for B
    assert c['funnel']['active_references'] == 0
    assert c['funnel']['staging_percent'] is None
    assert c['funnel']['unique_searchable_hackathons'] == 0


def test_funnel_empty_database_reports_no_denominators_not_fake_zero_percent():
    snap = CoverageRepository(FakeConnection(empty=True)).snapshot(as_of=NOW)
    funnel = snap['conversion_funnel']
    assert funnel['active_references'] == 0
    assert funnel['unique_searchable_hackathons'] == 0
    assert funnel['acceptance_percent'] is None
    assert funnel['staging_percent'] is None
    assert funnel['fetch_percent'] is None
    assert snap['sources'] == []


def test_stage_sql_counts_only_accepted_and_current_nonmerged_hackathons():
    db = FakeConnection()
    CoverageRepository(db).snapshot(as_of=NOW)
    sql = next(sql for sql, _ in db.queries if 'GROUPING(r.source_id)' in sql)
    assert 'GROUP BY GROUPING SETS ((r.source_id), ())' in sql
    assert 'COUNT(DISTINCT h.id)' in sql
    assert 'p.last_error IS NULL' in sql
    assert 'p.last_error IS NOT NULL' in sql
    assert 'p.processed_hash = p.content_hash' in sql
    assert 'h.is_active AND h.merged_into_id IS NULL' in sql
    assert 'WHERE r.active AND r.source_id IS NOT NULL' in sql
    assert 'p.source_event_ref_id = r.id AND p.source_id = r.source_id' in sql
    assert 'source_url_backlog' not in sql  # never join URL rows to refs
    source_sql = next(sql for sql, _ in db.queries if 'FROM sources s' in sql)
    assert "kind = 'DISCOVERED'" in source_sql
    assert "kind = 'DISCOVERED' AND state = 'DONE'" in source_sql


def test_cli_and_browser_describe_stage_units_explicitly():
    markdown = render_markdown(CoverageRepository(FakeConnection()).snapshot(as_of=NOW))
    assert 'Source ingestion funnel' in markdown
    assert 'Discovered URL tasks currently DONE: **8**' in markdown
    assert 'Accepted normalized references: **3**' in markdown
    assert 'Rejected normalized references: **1**' in markdown
    assert 'DISTINCT hackathons from those links: **2**' in markdown
    assert 'DIFFERENT units' in markdown
    component = (Path(__file__).resolve().parents[2] / 'web/components/AdminDashboard.tsx').read_text()
    assert 'Source ingestion funnel' in component
    assert 'conversion_funnel.unique_searchable_hackathons' in component
    assert 'Rejected records are not successful normalizations.' in component
