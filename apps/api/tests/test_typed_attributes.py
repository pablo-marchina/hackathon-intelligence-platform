"""Offline contracts for curated attributes projected from canonical evidence."""

from __future__ import annotations

import pytest
from hackathon_api.advanced_filters import (
    FilterField,
    compile_expression,
    filter_field_catalog,
    validate_expression,
)
from hackathon_api.attribute_catalog import expression_attribute_keys, load_attribute_fields
from hackathon_api.repositories.hackathons import build_hackathon_query
from hackathon_api.schemas import AdvancedHackathonFilters

LANGUAGE = FilterField(
    kind="text",
    operators=("eq", "ne", "in", "is_known", "is_unknown", "contains"),
    attribute_key="language",
)
CAPACITY = FilterField(
    kind="number",
    operators=("eq", "ne", "in", "is_known", "is_unknown", "gt", "gte", "lt", "lte", "between"),
    attribute_key="capacity",
)
FIELDS = {"attrs.language": LANGUAGE, "attrs.capacity": CAPACITY}


def test_attribute_keys_are_discovered_recursively_without_db_calls() -> None:
    expression = {
        "and": [
            {"field": "format", "op": "eq", "value": "ONLINE"},
            {"not": {"field": "attrs.language", "op": "eq", "value": "en"}},
            {
                "or": [
                    {"field": "attrs.capacity", "op": "gte", "value": 500},
                    {"field": "attrs.language", "op": "is_unknown"},
                ]
            },
        ]
    }
    assert expression_attribute_keys(expression) == {"language", "capacity"}
    validate_expression(expression)  # Before the database is accessed.
    params: dict = {"limit": 30}
    sql = compile_expression(expression, params, FIELDS)
    assert "canonical_field_selections cfs" in sql
    assert "source_observations so" in sql
    assert "cfs.active=true" in sql
    assert "so.normalized_value_json" in sql
    assert "language" not in sql and "capacity" not in sql
    assert "attrs.language" not in sql
    assert list(params.values()) == [30, "ONLINE", "language", "en", "capacity", 500, "language"]


def test_attribute_sql_has_type_guards_and_unknown_semantics() -> None:
    expr = {"field": "attrs.capacity", "op": "is_unknown"}
    params: dict = {}
    sql = compile_expression(expr, params, FIELDS)
    assert "jsonb_typeof(so.normalized_value_json)='number'" in sql
    assert "IS NULL" in sql
    assert params["advanced_0"] == "capacity"
    assert "#>> '{}'" in sql


def test_approved_types_disallow_unsupported_operators_and_values() -> None:
    for expr in [
        {"field": "attrs.capacity", "op": "contains", "value": "100"},
        {"field": "attrs.language", "op": "gte", "value": 23},
        {"field": "attrs.capacity", "op": "gte", "value": "free"},
        {"field": "attrs.language", "op": "eq", "value": True},
    ]:
        with pytest.raises(ValueError):
            compile_expression(expr, {}, FIELDS)


@pytest.mark.parametrize(
    "key",
    [
        "attrs.language); DROP TABLE hackathons;--",
        "attrs.__dict__",
        "attrs.ABC",
        "attrs.a" + "a" * 100,
        "attributes.capacity",
        "attrs.key.with.dot",
    ],
)
def test_malicious_or_invalid_attribute_identifiers_rejected(key: str) -> None:
    with pytest.raises(ValueError):
        validate_expression({"field": key, "op": "is_known"})


def test_unregistered_dynamic_key_must_be_rejected_by_compiler() -> None:
    filters = AdvancedHackathonFilters(
        expression={"field": "attrs.not_approved", "op": "eq", "value": "x"}
    )
    with pytest.raises(ValueError, match="unknown or non-filterable"):
        build_hackathon_query(filters, dynamic_fields={})


def test_dynamic_query_compiles_only_with_explicit_allowlist() -> None:
    filters = AdvancedHackathonFilters(
        expression={"field": "attrs.language", "op": "eq", "value": "pt"}
    )
    spec = build_hackathon_query(filters, dynamic_fields=FIELDS)
    assert "canonical_field_selections" in spec.sql
    assert spec.params["advanced_0"] == "language"
    assert spec.params["advanced_1"] == "pt"
    assert "'pt'" not in spec.sql


def test_catalog_includes_curated_attributes_without_changing_static_registry() -> None:
    result = filter_field_catalog(FIELDS)
    attrs = {row["key"]: row for row in result["fields"]}
    assert attrs["attrs.capacity"]["type"] == "number"
    assert "gte" in attrs["attrs.capacity"]["operators"]
    assert attrs["attrs.language"]["type"] == "text"
    assert "attrs.capacity" not in {row["key"] for row in filter_field_catalog()["fields"]}


def test_dynamic_database_registry_is_inactive_by_default() -> None:
    class Rows:
        def mappings(self):
            return self

        def all(self):
            return [
                {"key": "language", "data_type": "text", "description": "Language", "unit": None},
                {
                    "key": "capacity",
                    "data_type": "number",
                    "description": "Attendees",
                    "unit": "attendees",
                },
            ]

    class Connection:
        def execute(self, statement, params):
            assert "filterable=true" in str(statement)
            assert "active=true" in str(statement)
            assert params == {"keys": ["capacity", "language"]}
            return Rows()

    fields = load_attribute_fields(Connection(), {"language", "capacity"})
    assert fields["attrs.capacity"].attribute_key == "capacity"
    assert fields["attrs.language"].kind == "text"
    assert load_attribute_fields(Connection(), set()) == {}


def test_request_body_validation_is_pre_database_and_preserves_classic_fields() -> None:
    filters = AdvancedHackathonFilters(
        format=["ONLINE"],
        expression={"field": "attrs.language", "op": "eq", "value": "en"},
    )
    assert filters.format == ["ONLINE"]
    assert filters.expression is not None
