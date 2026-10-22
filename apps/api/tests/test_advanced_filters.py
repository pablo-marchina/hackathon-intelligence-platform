"""Contract tests for versioned composable hackathon search.

All validation and query tests are offline; SQL text uses a strict registry and
never interpolates untrusted values into its text.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from hackathon_api.advanced_filters import (
    FIELDS,
    MAX_DEPTH,
    compile_expression,
    filter_field_catalog,
    validate_expression,
)
from hackathon_api.db import connection_dependency
from hackathon_api.main import app
from hackathon_api.repositories.hackathons import (
    build_hackathon_query,
    decode_cursor,
    encode_cursor,
)
from hackathon_api.schemas import HackathonFilters
from pydantic import ValidationError


def test_nested_boolean_expression_compiles_with_bound_values() -> None:
    filters = HackathonFilters(
        expression={
            "and": [
                {"field": "status", "op": "eq", "value": "REGISTRATION_OPEN"},
                {
                    "or": [
                        {"field": "format", "op": "eq", "value": "ONLINE"},
                        {"field": "format", "op": "eq", "value": "HYBRID"},
                    ]
                },
                {"not": {"field": "is_free", "op": "eq", "value": False}},
                {"field": "eligible_country", "op": "eq", "value": "br"},
            ]
        }
    )
    spec = build_hackathon_query(filters)
    assert " AND " in spec.sql
    assert " OR " in spec.sql
    assert "NOT " in spec.sql
    assert "EXISTS (SELECT 1 FROM eligibility_countries ec" in spec.sql
    assert "REGISTRATION_OPEN" not in spec.sql
    assert "ONLINE" not in spec.sql
    assert "BR" not in spec.sql
    assert list(spec.params[k] for k in sorted(spec.params) if k.startswith("advanced_")) == [
        "REGISTRATION_OPEN",
        "ONLINE",
        "HYBRID",
        False,
        "BR",
    ]


def test_range_numeric_and_datetime_and_missing_value_semantics() -> None:
    spec = build_hackathon_query(
        HackathonFilters(
            expression={
                "and": [
                    {"field": "prize_pool_usd", "op": "gte", "value": 1500},
                    {"field": "maximum_team_size", "op": "between", "value": [1, 4]},
                    {"field": "start_at", "op": "gte", "value": "2026-11-01T00:00:00-03:00"},
                    {"field": "registration_close_at", "op": "is_known"},
                    {"field": "minimum_age", "op": "is_unknown"},
                ]
            }
        )
    )
    assert "MAX(pp.normalized_usd_amount)" in spec.sql
    assert "BETWEEN :advanced_1 AND :advanced_2" in spec.sql
    assert "h.registration_close_at IS NOT NULL" in spec.sql
    assert "er.minimum_age" in spec.sql
    assert "IS NULL" in spec.sql
    assert spec.params["advanced_0"] == 1500
    assert spec.params["advanced_1"] == 1
    assert spec.params["advanced_2"] == 4
    assert isinstance(spec.params["advanced_3"], datetime)
    assert spec.params["advanced_3"].utcoffset() is not None


def test_search_over_existing_relationship_tables() -> None:
    spec = build_hackathon_query(
        HackathonFilters(
            expression={
                "or": [
                    {"field": "technology", "op": "in", "value": ["python", "javascript"]},
                    {"field": "track", "op": "eq", "value": "fintech"},
                    {"field": "source_domain", "op": "eq", "value": "mlh.io"},
                ]
            }
        )
    )
    assert "hackathon_technologies hx JOIN technologies tx" in spec.sql
    assert "hackathon_tracks ht JOIN tracks t" in spec.sql
    assert "source_event_refs sr JOIN sources s" in spec.sql
    assert spec.params["advanced_0"] == ["python", "javascript"]
    assert spec.params["advanced_1"] == "fintech"
    assert spec.params["advanced_2"] == "mlh.io"


def test_contains_does_not_treat_sql_wildcards_as_pattern() -> None:
    malicious = "%' OR 1=1 --\\_"
    params = {}
    query = compile_expression({"field": "name", "op": "contains", "value": malicious}, params)
    assert "POSITION(LOWER(" in query
    assert malicious not in query
    assert params["advanced_0"] == malicious


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"and": []},
        {"or": [42]},
        {"not": {"field": "name", "op": "eq"}},
        {"field": "unknown_column", "op": "eq", "value": "x"},
        {"field": "name; DROP TABLE hackathons;--", "op": "eq", "value": "x"},
        {"field": "name", "op": "SQL", "value": "x"},
        {"field": "name", "op": "eq", "value": None},
        {"field": "is_free", "op": "eq", "value": "true"},
        {"field": "minimum_age", "op": "eq", "value": True},
        {"field": "minimum_age", "op": "eq", "value": float("nan")},
        {"field": "start_at", "op": "eq", "value": "2026-01-01"},
        {"field": "country_code", "op": "eq", "value": "BRA"},
        {"field": "track", "op": "contains", "value": "hack"},
        {
            "field": "start_at",
            "op": "between",
            "value": ["2027-01-01T00:00:00Z", "2026-01-01T00:00:00Z"],
        },
        {"field": "minimum_age", "op": "between", "value": [18]},
        {"field": "name", "op": "is_known", "value": True},
        {"field": "name", "op": "in", "value": []},
        {"field": "name", "op": "in", "value": ["a"] * 21},
        {"field": "name", "op": "eq", "value": "a", "sql": "1=1"},
        {"and": [{"field": "name", "op": "is_known"}] * 33},
        {"not": {"not": {"not": {"not": {"not": {"not": {"field": "name", "op": "is_known"}}}}}}},
    ],
)
def test_malformed_expressions_rejected_before_db(invalid: dict) -> None:
    with pytest.raises((ValueError, ValidationError)):
        HackathonFilters(expression=invalid)


def test_cost_budget_for_deep_or_expensive_groups() -> None:
    expr: dict = {"field": "name", "op": "eq", "value": "Hackathon"}
    for _ in range(MAX_DEPTH + 1):
        expr = {"not": expr}
    with pytest.raises(ValueError, match="complexity"):
        validate_expression(expr)
    with pytest.raises(ValueError):
        validate_expression({"or": [{"field": "name", "op": "is_known"}] * 13})


def test_cursor_fingerprint_includes_advanced_predicate() -> None:
    row = {"id": "00000000-0000-0000-0000-000000000001"}
    filters = HackathonFilters(expression={"field": "format", "op": "eq", "value": "ONLINE"})
    cursor = encode_cursor(row, filters)
    assert decode_cursor(cursor)["id"] == row["id"]
    assert build_hackathon_query(filters.model_copy(update={"cursor": cursor})).sql
    with pytest.raises(ValueError, match="cursor does not match"):
        build_hackathon_query(
            HackathonFilters(
                expression={"field": "format", "op": "eq", "value": "HYBRID"},
                cursor=cursor,
            )
        )


def test_filter_field_catalog_is_driven_by_compiler_registry() -> None:
    catalog = filter_field_catalog()
    assert catalog["schema_version"] == "advanced-filter-v1"
    assert {field["key"] for field in catalog["fields"]} == set(FIELDS)
    assert all(field["operators"] for field in catalog["fields"])


def test_routes_documented_in_openapi() -> None:
    paths = app.openapi()["paths"]
    assert "post" in paths["/v1/hackathons/query"]
    assert "get" in paths["/v1/hackathons/filter-fields"]


def test_invalid_expression_http_rejected_before_connection() -> None:
    response = TestClient(app).post(
        "/v1/hackathons/query",
        json={"expression": {"field": "name", "op": "sql", "value": "evil"}},
    )
    assert response.status_code == 422


def test_post_query_e2e_with_fake_database_and_bound_filter_values() -> None:
    class Rows:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "id": UUID("00000000-0000-0000-0000-000000000001"),
                    "slug": "sample-hackathon",
                    "name": "Sample Hackathon",
                    "event_type": "hackathon",
                    "status": "REGISTRATION_OPEN",
                    "format": "ONLINE",
                    "source_count": 2,
                }
            ]

    class FakeConnection:
        def execute(self, sql, params):
            assert "h.format::text" in str(sql)
            assert params["advanced_0"] == "ONLINE"
            assert "ONLINE" not in str(sql)
            return Rows()

    app.dependency_overrides[connection_dependency] = lambda: FakeConnection()
    try:
        response = TestClient(app).post(
            "/v1/hackathons/query",
            json={"expression": {"field": "format", "op": "eq", "value": "ONLINE"}},
        )
    finally:
        app.dependency_overrides.pop(connection_dependency, None)
    assert response.status_code == 200
    assert response.json()["items"][0]["slug"] == "sample-hackathon"
    assert response.json()["next_cursor"] is None
