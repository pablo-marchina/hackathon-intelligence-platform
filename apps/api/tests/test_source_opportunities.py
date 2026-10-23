"""Contract tests for per-source marginal coverage, metadata gaps and access."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hackathon_api.db import connection_dependency
from hackathon_api.main import app
from hackathon_api.repositories.source_opportunities import SourceOpportunityRepository
from hackathon_api.security import AdminPrincipal, admin_principal, require_admin

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


class Rows:
    def __init__(self, values: Any) -> None:
        self.values = values

    def mappings(self) -> Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.values

    def one(self) -> dict[str, Any]:
        return self.values


class FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []

    def execute(self, stmt: Any, params: Any = None) -> Rows:
        sql = str(stmt)
        self.executed.append((sql, params))
        if "gap_summary" in sql:
            return Rows(
                [
                    {
                        "source_id": "first",
                        "name": "First",
                        "permission_status": "ALLOWED",
                        "active": True,
                        "discovery_enabled": True,
                        "detail_extraction_enabled": True,
                        "ingestion_mode": "API",
                        "priority": 60,
                        "attributed_unique_active": 2,
                        "exclusive_canonicals": 1,
                        "shared_canonicals": 1,
                        "missing_registration_deadline": 1,
                        "unknown_cost": 2,
                        "missing_prize_details": 1,
                        "recent_created": 1,
                        "recent_linked": 1,
                    },
                    {
                        "source_id": "review",
                        "name": "Unapproved",
                        "permission_status": "REVIEW_REQUIRED",
                        "active": True,
                        "discovery_enabled": False,
                        "detail_extraction_enabled": False,
                        "ingestion_mode": "DISCOVERY_ONLY",
                        "priority": 20,
                        "attributed_unique_active": 1,
                        "exclusive_canonicals": 0,
                        "shared_canonicals": 1,
                    },
                    {
                        "source_id": "empty",
                        "name": "Empty",
                        "permission_status": "ALLOWED",
                        "active": True,
                        "discovery_enabled": True,
                        "detail_extraction_enabled": True,
                        "ingestion_mode": "API",
                        "priority": 80,
                    },
                ]
            )
        if "provider_count" in sql:
            return Rows(
                {
                    "total_active_canonicals": 4,
                    "canonicals_with_active_source": 2,
                    "exclusive_to_one_source": 1,
                    "shared_across_sources": 1,
                }
            )
        raise AssertionError("unexpected SQL")


def test_snapshot_counts_unique_canonicals_and_avoids_additive_source_totals() -> None:
    db = FakeConnection()
    report = SourceOpportunityRepository(db).snapshot(as_of=NOW)
    assert report["global"] == {
        "total_active_canonicals": 4,
        "canonicals_with_active_source": 2,
        "exclusive_to_one_source": 1,
        "shared_across_sources": 1,
        "without_active_source": 2,
    }
    first, denied, empty = report["sources"]
    assert first["exclusive_canonicals"] == 1
    assert first["exclusive_percent"] == 50.0
    assert first["known_registration_deadline_percent"] == 50.0
    assert first["known_cost_percent"] == 0.0
    assert first["suggested_next_step"] == "ENRICH_CANONICAL_FIELDS"
    assert denied["eligible_for_controlled_collection"] is False
    assert denied["suggested_next_step"] == "REVIEW_PERMISSIONS_OR_PAUSE"
    assert empty["eligible_for_controlled_collection"] is True
    assert empty["suggested_next_step"] == "VALIDATE_WITH_CONTROLLED_COLLECTION"
    assert empty["exclusive_percent"] is None
    assert "MUST NOT be summed" in report["methodology"]["global"]
    assert report["as_of"].endswith("+00:00")
    assert db.executed[0][1]["limit"] == 100
    assert db.executed[0][1]["since"] < db.executed[0][1]["as_of"]


def test_sql_uses_distinct_source_event_pairs_and_filters_inactive() -> None:
    db = FakeConnection()
    SourceOpportunityRepository(db).snapshot(as_of=NOW, days=30, limit=5)
    query = db.executed[0][0]
    assert "SELECT DISTINCT r.source_id, r.hackathon_id" in query
    assert "r.active AND r.source_id IS NOT NULL" in query
    assert "h.is_active AND h.merged_into_id IS NULL" in query
    assert "p.provider_count=1" in query
    assert "COUNT(*) FILTER (WHERE p.provider_count>1)" in query
    assert "COUNT(*) FILTER (WHERE outcome='CREATED')" in query
    assert "COUNT(*) FILTER (WHERE h.registration_close_at IS NULL)" in query
    assert "NOT EXISTS" in query
    assert "WHERE processed_at >= :since AND processed_at <= :as_of" in query
    assert "LIMIT :limit" in query


@pytest.mark.parametrize("days,limit", [(0, 5), (91, 5), (30, 0), (30, 101)])
def test_invalid_window_and_limit_rejected_before_database(days: int, limit: int) -> None:
    db = FakeConnection()
    with pytest.raises(ValueError):
        SourceOpportunityRepository(db).snapshot(days=days, limit=limit)
    assert db.executed == []


def test_naive_datetime_is_rejected_before_sql() -> None:
    db = FakeConnection()
    with pytest.raises(ValueError, match="timezone"):
        SourceOpportunityRepository(db).snapshot(as_of=datetime(2026, 10, 23))
    assert db.executed == []


def test_admin_endpoint_protected_and_has_bounded_query_contract() -> None:
    paths = app.openapi()["paths"]
    assert "/admin/coverage/opportunities" in paths
    db = FakeConnection()
    with TestClient(app) as client:
        assert client.get("/admin/coverage/opportunities").status_code in {401, 503}
        app.dependency_overrides[require_admin] = lambda: None
        app.dependency_overrides[admin_principal] = lambda: AdminPrincipal(
            None, "API_KEY", "VIEWER"
        )
        app.dependency_overrides[connection_dependency] = lambda: db
        try:
            ok = client.get("/admin/coverage/opportunities?recent_days=7&limit=5")
            assert ok.status_code == 200, ok.text
            assert ok.json()["sources"][0]["exclusive_percent"] == 50.0
            assert client.get("/admin/coverage/opportunities?recent_days=365").status_code == 422
            assert client.get("/admin/coverage/opportunities?limit=101").status_code == 422
        finally:
            app.dependency_overrides.clear()
