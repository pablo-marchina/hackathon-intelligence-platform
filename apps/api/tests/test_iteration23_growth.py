"""Iteration 23: observed catalog growth is not an unverified discovery claim."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from hackathon_api.db import connection_dependency
from hackathon_api.main import app
from hackathon_api.repositories.coverage import CoverageRepository
from hackathon_api.security import AdminPrincipal, admin_principal, require_admin
from test_iteration17_coverage import NOW, FakeConnection, Rows

from scripts.catalog_coverage_report import render_markdown


class GrowthConnection(FakeConnection):
    def execute(self, query, params=None):
        if "new_active_canonicals" in str(query) and "FROM hackathons h" in str(query):
            self.queries.append((str(query), params))
            return Rows(
                [
                    {"day": datetime(2026, 10, 17).date(), "new_active_canonicals": 1},
                    {"day": datetime(2026, 10, 19).date(), "new_active_canonicals": 2},
                ]
            )
        if "FROM sources s" in str(query):
            rows = super().execute(query, params).all()
            rows[0]["new_source_refs_recent"] = 8
            rows[0]["new_attributed_canonicals_recent"] = 2
            rows[1]["new_source_refs_recent"] = 5
            rows[1]["new_attributed_canonicals_recent"] = 2
            return Rows(rows)
        return super().execute(query, params)


def test_daily_growth_fills_missing_days_without_inflating_global_total():
    db = GrowthConnection()
    snapshot = CoverageRepository(db).snapshot(as_of=NOW, recent_days=3)
    assert snapshot["growth"] == {
        "calendar_timezone": "UTC",
        "calendar_days": 3,
        "new_active_canonicals": 3,
        "daily": [
            {"date": "2026-10-17", "new_active_canonicals": 1},
            {"date": "2026-10-18", "new_active_canonicals": 0},
            {"date": "2026-10-19", "new_active_canonicals": 2},
        ],
    }
    assert snapshot["catalog"]["unique_active"] == 3
    assert sum(row["new_attributed_canonicals_recent"] for row in snapshot["sources"]) == 4
    assert snapshot["growth"]["new_active_canonicals"] == 3
    sql, params = db.queries[-1]
    assert "AT TIME ZONE 'UTC'" in sql
    assert "h.is_active AND h.merged_into_id IS NULL" in sql
    assert params["growth_start"] == datetime(2026, 10, 17, tzinfo=UTC)
    assert params["as_of"] == NOW
    assert "includes imports and backfills" in snapshot["methodology"]["new_active_canonicals"]


def test_growth_handles_empty_catalog_and_single_day():
    result = CoverageRepository(FakeConnection(empty=True)).snapshot(as_of=NOW, recent_days=1)
    assert result["growth"]["daily"] == [{"date": "2026-10-19", "new_active_canonicals": 0}]
    assert result["growth"]["new_active_canonicals"] == 0


def test_growth_normalizes_non_utc_time_and_not_future_dates():
    offset = NOW.astimezone(__import__("datetime").timezone(timedelta(hours=-3)))
    db = FakeConnection(empty=True)
    result = CoverageRepository(db).snapshot(as_of=offset, recent_days=2)
    assert result["as_of"] == NOW.isoformat()
    assert result["growth"]["daily"][0]["date"] == "2026-10-18"
    daily_sql, params = db.queries[-1]
    assert params["as_of"] == NOW
    assert "h.created_at <= :as_of" in daily_sql


def test_coverage_endpoint_accepts_bounded_calendar_window():
    conn = GrowthConnection()
    with TestClient(app) as client:
        app.dependency_overrides[require_admin] = lambda: None
        app.dependency_overrides[admin_principal] = lambda: AdminPrincipal(
            None, "API_KEY", "VIEWER"
        )
        app.dependency_overrides[connection_dependency] = lambda: conn
        try:
            response = client.get("/admin/coverage?recent_days=3")
            assert response.status_code == 200
            assert response.json()["growth"]["calendar_days"] == 3
            assert client.get("/admin/coverage?recent_days=91").status_code == 422
            assert client.get("/admin/coverage?recent_days=0").status_code == 422
        finally:
            app.dependency_overrides.clear()


def test_markdown_labels_backfills_and_source_overlap():
    text = render_markdown(CoverageRepository(GrowthConnection()).snapshot(as_of=NOW))
    assert "includes historical backfills" in text
    assert "| 2026-10-19 | 2 |" in text
    assert "New refs (rolling)" in text
    assert "| A | ALLOWED | True | True | 8 | 3 | 8 | 2 |" in text
