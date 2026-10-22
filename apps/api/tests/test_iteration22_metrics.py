"""Read-only coverage is actionable without confusing source records with events."""
from datetime import UTC, datetime

from hackathon_api.repositories.coverage import CoverageRepository
from scripts.catalog_coverage_report import render_markdown

from test_iteration17_coverage import FakeConnection


INSTANT = datetime(2026, 10, 21, 12, tzinfo=UTC)


def test_report_exposes_staging_retry_queue_and_filter_completeness():
    snap = CoverageRepository(FakeConnection()).snapshot(as_of=INSTANT)
    markdown = render_markdown(snap)
    assert 'Unique active hackathons: **3**' in markdown
    assert 'Records awaiting normalization: **2**' in markdown
    assert 'Records with normalization errors: **1**' in markdown
    assert 'URLs ready for crawl: **3**' in markdown
    assert 'URLs exhausted: **2**' in markdown
    assert 'Organizer: **66.7%**' in markdown
    assert 'Sponsor: **33.3%**' in markdown
    assert 'Eligibility rules: **0.0%**' in markdown
    assert '| A | ALLOWED | True | True | 8 | 3 | 0 | 0 | 3 | 2 |' in markdown


def test_empty_catalog_does_not_report_zero_percent_information_coverage():
    snap = CoverageRepository(FakeConnection(empty=True)).snapshot(as_of=INSTANT)
    markdown = render_markdown(snap)
    assert 'Unique active hackathons: **0**' in markdown
    assert 'Known format: **N/A (empty catalog)**' in markdown
    assert 'Sponsor: **N/A (empty catalog)**' in markdown
    assert 'URLs ready for crawl: **0**' in markdown
