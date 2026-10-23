"""Bounds, SQL safety, unknown values and full-cohort faceting contracts."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from hackathon_api.main import app
from hackathon_api.repositories.search_facets import (
    HackathonFacetRepository,
    validate_facet_fields,
)
from hackathon_api.routes import hackathons as routes
from hackathon_api.schemas import AdvancedHackathonFilters, HackathonFacetRequest
from pydantic import ValidationError


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.queries = []

    def execute(self, sql, params=None):
        query = str(sql)
        self.queries.append((query, params))
        if "attribute_definitions" in query:
            return Rows(
                [
                    {
                        "key": "language",
                        "data_type": "text",
                        "description": "Language",
                        "unit": None,
                    },
                    {
                        "key": "team_capacity",
                        "data_type": "number",
                        "description": "Capacity",
                        "unit": None,
                    },
                ]
            )
        if "SELECT COUNT(*) FROM matched" in query:
            return Rows(43)
        if "GROUP BY b.value" in query:
            return Rows(
                [
                    {"value": "ONLINE", "count": 29},
                    {"value": None, "count": 14},
                ]
            )
        raise AssertionError("unexpected SQL in facet repository")


def test_facet_fields_are_strict_bounded_and_deduplicated() -> None:
    assert validate_facet_fields(["format", "source_domain"]) == [
        "format",
        "source_domain",
    ]
    for bad in [
        [],
        ["format"] * 2,
        ["format"] * 5,
        ["name"],
        ["minimum_age"],
        ["attrs.foo);DROP TABLE sources;--"],
        ["attrs.Upper"],
    ]:
        with pytest.raises(ValueError):
            validate_facet_fields(bad)


def test_full_facet_query_uses_same_complex_matching_without_limit_or_cursor() -> None:
    connection = FakeConnection()
    filters = AdvancedHackathonFilters(
        limit=1,
        sort="deadline",
        expression={
            "and": [
                {"field": "format", "op": "eq", "value": "ONLINE"},
                {"field": "attrs.language", "op": "eq", "value": "pt"},
            ]
        },
    )
    result = HackathonFacetRepository(connection).summarize(
        filters, fields=["format", "attrs.language", "source_domain"], bucket_limit=5
    )
    assert result["matched_total"] == 43
    assert result["counts_scope"] == "ALL_MATCHES_NOT_CURRENT_PAGE"
    assert result["facets"]["format"][1] == {"value": None, "count": 14}
    assert len([q for q, p in connection.queries if "GROUP BY b.value" in q]) == 3
    for query, params in connection.queries:
        if "WITH matched" not in query:
            continue
        assert "h.is_active=true" in query
        assert "h.format::text" in query
        assert "canonical_field_selections cfs" in query
        assert "ORDER BY h." not in query
        assert "cursor_" not in str(params)
        assert "LIMIT :limit" not in query
        assert params["advanced_0"] == "ONLINE"
        assert params["advanced_1"] == "language"
        assert params["advanced_2"] == "pt"
        assert "pt" not in query and "'ONLINE'" not in query
    dynamic = [p for q, p in connection.queries if "facet_attribute_path" in p]
    assert len(dynamic) == 1
    assert dynamic[0]["facet_attribute_path"] == "language"
    assert any(":facet_attribute_path" in query for query, _ in connection.queries)


def test_numeric_dynamic_facet_refused_without_exposing_untrusted_sql() -> None:
    connection = FakeConnection()
    with pytest.raises(ValueError, match="numeric"):
        HackathonFacetRepository(connection).summarize(
            AdvancedHackathonFilters(), fields=["attrs.team_capacity"]
        )
    assert len([x for x in connection.queries if "GROUP BY b.value" in x[0]]) == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"fields": []},
        {"fields": ["format", "format"]},
        {"fields": ["format"] * 5},
        {"fields": ["totally_fake"]},
        {"fields": ["attrs.nasty;drop"]},
        {"fields": ["format"], "bucket_limit": 16},
        {"fields": ["format"], "cursor": "garbage"},
        {"expression": {"field": "format", "op": "sql", "value": "ONLINE"}},
    ],
)
def test_invalid_facet_request_fails_pydantic_before_database(payload: dict) -> None:
    with pytest.raises(ValidationError):
        HackathonFacetRequest.model_validate(payload)


def test_facet_http_validation_never_opens_connection(monkeypatch) -> None:
    class Engine:
        @contextmanager
        def begin(self):
            pytest.fail("invalid request opened database")

    monkeypatch.setattr(routes, "get_engine", lambda: Engine())
    client = TestClient(app)
    for body in [{"fields": ["evil"]}, {"fields": ["format"], "bucket_limit": 100}]:
        response = client.post("/v1/hackathons/facets", json=body)
        assert response.status_code == 422
    assert "post" in app.openapi()["paths"]["/v1/hackathons/facets"]


def test_facet_api_with_fake_database(monkeypatch) -> None:
    db = FakeConnection()

    class Engine:
        @contextmanager
        def begin(self):
            yield db

    monkeypatch.setattr(routes, "get_engine", lambda: Engine())
    resp = TestClient(app).post(
        "/v1/hackathons/facets",
        json={
            "fields": ["format"],
            "expression": {"field": "format", "op": "eq", "value": "ONLINE"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["matched_total"] == 43
    assert resp.json()["facets"]["format"][0]["count"] == 29
