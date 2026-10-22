"""Iteration 17: live coverage counts must never be inferred from config or historical W2."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from hackathon_api.db import connection_dependency
from hackathon_api.main import app
from hackathon_api.repositories.coverage import CoverageRepository
from hackathon_api.security import AdminPrincipal, admin_principal, require_admin

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
COUNTS = {
    "unique_active": 3,
    "upcoming_by_date": 1,
    "ongoing_by_date": 1,
    "confirmed_registration_window": 1,
    "unknown_registration_close": 2,
    "with_official_url": 2,
    "with_start_end": 1,
    "with_primary_location": 1,
    "inactive_or_retired": 1,
}


COMPLETENESS = {
    "known_format": 2,
    "with_registration_url": 1,
    "with_registration_deadline": 1,
    "known_participation_cost": 2,
    "known_worldwide_eligibility": 1,
    "with_description": 2,
    "verified_recently": 1,
    "with_organizer": 2,
    "with_sponsor": 1,
    "with_tracks": 1,
    "with_technologies": 2,
    "with_prizes": 1,
    "with_eligibility_rules": 0,
}
PIPELINE = {
    "staged_payloads": 5,
    "awaiting_normalization": 2,
    "normalized_current_version": 3,
    "normalization_errors": 1,
}
BACKLOG = {
    "backlog_pending": 4,
    "backlog_retry": 1,
    "backlog_claimed": 1,
    "backlog_done": 10,
    "backlog_exhausted": 2,
    "backlog_ready_now": 3,
    "backlog_expired_claims": 1,
}


class Rows:
    def __init__(self, payload):
        self.payload = payload

    def mappings(self):
        return self

    def one(self):
        return self.payload

    def all(self):
        return self.payload


class FakeConnection:
    def __init__(self, *, empty=False):
        self.queries = []
        self.empty = empty

    def execute(self, query, params=None):
        sql = str(query)
        self.queries.append((sql, params))
        if "FROM hackathons h" in sql and "unique_active" in sql:
            return Rows({key: 0 for key in COUNTS} if self.empty else COUNTS)
        if "FROM source_event_payloads" in sql:
            return Rows({key: 0 for key in PIPELINE} if self.empty else PIPELINE)
        if "FROM hackathons h" in sql and "known_format" in sql:
            return Rows({key: 0 for key in COMPLETENESS} if self.empty else COMPLETENESS)
        if "GROUPING(r.source_id)" in sql and "FROM source_event_refs r" in sql:
            if self.empty:
                return Rows([{"is_global": 1, "source_id": None}])
            return Rows(
                [
                    {
                        "is_global": 1,
                        "source_id": None,
                        "active_references": 13,
                        "staged_references": 5,
                        "pending_references": 1,
                        "accepted_references": 3,
                        "rejected_references": 1,
                        "failed_pending_references": 1,
                        "linked_searchable_references": 3,
                        "unique_searchable_hackathons": 2,
                    },
                    {
                        "is_global": 0,
                        "source_id": "a",
                        "active_references": 8,
                        "staged_references": 3,
                        "pending_references": 0,
                        "accepted_references": 2,
                        "rejected_references": 1,
                        "linked_searchable_references": 2,
                        "unique_searchable_hackathons": 2,
                    },
                    {
                        "is_global": 0,
                        "source_id": "b",
                        "active_references": 5,
                        "staged_references": 2,
                        "pending_references": 1,
                        "accepted_references": 1,
                        "failed_pending_references": 1,
                        "linked_searchable_references": 1,
                        "unique_searchable_hackathons": 1,
                    },
                ]
            )
        if "new_active_canonicals" in sql and "FROM hackathons h" in sql:
            return Rows([] if self.empty else [{"day": NOW.date(), "new_active_canonicals": 2}])
        if "FROM sources s" in sql:
            if self.empty:
                return Rows([])
            return Rows(
                [
                    {
                        "source_id": "a",
                        "name": "A",
                        "domain": "a.example",
                        "permission_status": "ALLOWED",
                        "active": True,
                        "last_success_at": NOW - timedelta(hours=1),
                        "source_refs": 8,
                        "attributed_unique_active": 3,
                        "discovered_urls": 12,
                        "fetched_discovered_urls": 8,
                        **BACKLOG,
                    },
                    {
                        "source_id": "b",
                        "name": "B",
                        "domain": "b.example",
                        "permission_status": "REVIEW_REQUIRED",
                        "active": True,
                        "last_success_at": None,
                        "source_refs": 5,
                        "attributed_unique_active": 2,
                        "discovered_urls": 0,
                        "fetched_discovered_urls": 0,
                        **{k: 0 for k in BACKLOG},
                    },
                    {
                        "source_id": "c",
                        "name": "C",
                        "domain": "c.example",
                        "permission_status": "ALLOWED",
                        "active": False,
                        "last_success_at": NOW - timedelta(days=10),
                        "source_refs": 0,
                        "attributed_unique_active": 0,
                        "discovered_urls": 0,
                        "fetched_discovered_urls": 0,
                        **{k: 0 for k in BACKLOG},
                    },
                ]
            )
        raise AssertionError("Unexpected SQL")


def test_snapshot_separates_global_count_from_overlapping_source_attribution():
    db = FakeConnection()
    report = CoverageRepository(db).snapshot(as_of=NOW)
    assert report["catalog"]["unique_active"] == 3
    assert sum(s["attributed_unique_active"] for s in report["sources"]) == 5
    assert report["source_counts"] == {
        "registered_in_database": 3,
        "active_in_database": 2,
        "approved_and_active": 1,
        "recent_success": 1,
    }
    assert report["catalog"]["confirmed_registration_window"] == 1
    assert report["catalog"]["unknown_registration_close"] == 2
    assert report["sources"][0]["recent_success"] is True
    assert report["pipeline"]["awaiting_normalization"] == 2
    assert report["pipeline"]["normalization_errors"] == 1
    assert report["pipeline"]["backlog"]["backlog_pending"] == 4
    assert report["pipeline"]["backlog"]["backlog_ready_now"] == 3
    assert report["field_completeness"]["percentages"]["known_format"] == 66.7
    assert report["field_completeness"]["percentages"]["with_registration_url"] == 33.3
    assert report["field_completeness"]["percentages"]["with_sponsor"] == 33.3
    assert all("password" not in sql.lower() for sql, _ in db.queries)


def test_snapshot_requires_tz_aware_time_and_bounded_recency():
    db = FakeConnection()
    with pytest.raises(ValueError, match="timezone-aware"):
        CoverageRepository(db).snapshot(as_of=datetime(2026, 10, 19))
    with pytest.raises(ValueError, match="recent_days"):
        CoverageRepository(db).snapshot(recent_days=365)
    assert db.queries == []


def test_sql_counts_only_verified_registration_windows():
    db = FakeConnection()
    CoverageRepository(db).snapshot(as_of=NOW)
    catalog_sql = db.queries[0][0]
    assert "registration_open_at IS NOT NULL" in catalog_sql
    assert "registration_close_at IS NOT NULL" in catalog_sql
    assert "merged_into_id IS NULL" in catalog_sql
    assert "COUNT(DISTINCT h.id)" in db.queries[1][0]
    assert "FROM source_url_backlog b" in db.queries[1][0]
    assert "processed_hash IS DISTINCT FROM content_hash" in db.queries[2][0]
    assert "h.is_active AND h.merged_into_id IS NULL" in db.queries[3][0]


def test_coverage_endpoint_is_protected_and_returns_live_snapshot():
    conn = FakeConnection()
    with TestClient(app) as client:
        # Unauthenticated visitors must not read operational source metrics.
        assert client.get("/admin/coverage").status_code in {401, 503}
        app.dependency_overrides[require_admin] = lambda: None
        app.dependency_overrides[admin_principal] = lambda: AdminPrincipal(
            None, "API_KEY", "VIEWER"
        )
        app.dependency_overrides[connection_dependency] = lambda: conn
        try:
            response = client.get("/admin/coverage")
            assert response.status_code == 200, response.text
            assert response.json()["catalog"]["unique_active"] == 3
            assert response.json()["source_counts"]["registered_in_database"] == 3
        finally:
            app.dependency_overrides.clear()


def test_cli_markdown_labels_counts_as_live_not_history():
    from scripts.catalog_coverage_report import render_markdown

    result = CoverageRepository(FakeConnection()).snapshot(as_of=NOW)
    text = render_markdown(result)
    assert "Unique active hackathons: **3**" in text
    assert "historical baseline" in text
    assert "counts overlap" in text or "counts overlap" in text.casefold()


def test_empty_catalog_completeness_is_unknown_not_zero_percent():
    report = CoverageRepository(FakeConnection(empty=True)).snapshot(as_of=NOW)
    assert report["catalog"]["unique_active"] == 0
    assert report["field_completeness"]["counts"]["known_format"] == 0
    assert report["field_completeness"]["percentages"]["known_format"] is None
    assert report["pipeline"]["backlog"]["backlog_pending"] == 0
    assert report["sources"] == []


def test_source_backlog_is_counted_per_source_not_per_canonical():
    report = CoverageRepository(FakeConnection()).snapshot(as_of=NOW)
    assert report["catalog"]["unique_active"] == 3
    assert report["pipeline"]["backlog"]["backlog_pending"] == 4
    assert sum(row["attributed_unique_active"] for row in report["sources"]) == 5
    assert report["sources"][0]["backlog_exhausted"] == 2
    assert report["sources"][0]["backlog_expired_claims"] == 1
    assert report["pipeline"]["backlog"]["backlog_expired_claims"] == 1
    # Backlog-ready excludes claimed tasks even if their leases expired.
    assert report["pipeline"]["backlog"]["backlog_ready_now"] == 3
