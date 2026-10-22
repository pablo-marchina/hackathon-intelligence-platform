"""Curated attribute definitions projected from existing canonical field evidence.

Definitions do not authorize crawling and are not writable through the public API.
Only attributes selected in canonical_field_selections can be searched.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, text

from .advanced_filters import (
    BOOLEAN_OPERATORS,
    NUMBER_OPERATORS,
    TEXT_OPERATORS,
    FilterField,
)

TYPE_OPERATORS: dict[str, tuple[str, ...]] = {
    "text": TEXT_OPERATORS,
    "number": NUMBER_OPERATORS,
    "boolean": BOOLEAN_OPERATORS,
}


def load_attribute_fields(
    connection: Connection, keys: set[str] | None = None
) -> dict[str, FilterField]:
    """Resolve only curated active/filterable definitions from migrated PostgreSQL."""
    if keys is not None and not keys:
        return {}
    condition = " AND key = ANY(:keys)" if keys is not None else ""
    result = (
        connection.execute(
            text(
                "SELECT key, data_type, description, unit FROM attribute_definitions "
                "WHERE active=true AND filterable=true" + condition + " ORDER BY key"
            ),
            {"keys": sorted(keys)} if keys is not None else {},
        )
        .mappings()
        .all()
    )
    fields: dict[str, FilterField] = {}
    for row in result:
        kind = row["data_type"]
        if kind not in TYPE_OPERATORS:
            continue
        fields[f"attrs.{row['key']}"] = FilterField(
            kind=kind,
            operators=TYPE_OPERATORS[kind],
            attribute_key=row["key"],
            description=row["description"] or "",
        )
    return fields


def expression_attribute_keys(expression: dict[str, Any] | None) -> set[str]:
    """Find keys without SQL or DB access; full AST validation is a separate gate."""
    if expression is None:
        return set()
    if "field" in expression:
        name = expression["field"]
        return {name[6:]} if isinstance(name, str) and name.startswith("attrs.") else set()
    if "not" in expression:
        return expression_attribute_keys(expression["not"])
    for op in ("and", "or"):
        if op in expression:
            result: set[str] = set()
            for child in expression[op]:
                result.update(expression_attribute_keys(child))
            return result
    return set()


def selected_attributes(connection: Connection, hackathon_id: Any) -> list[dict[str, Any]]:
    """Render only typed, actively selected values alongside their source evidence."""
    rows = (
        connection.execute(
            text("""
            SELECT d.key, d.label, d.data_type, d.unit,
                   so.normalized_value_json AS value,
                   so.extraction_confidence AS confidence,
                   so.observed_at, sr.url AS source_url, s.name AS source_name
              FROM attribute_definitions d
              JOIN canonical_field_selections cfs
                ON cfs.field_path=d.key AND cfs.active=true
              JOIN source_observations so ON so.id=cfs.selected_observation_id
              LEFT JOIN source_event_refs sr ON sr.id=so.source_event_ref_id
              LEFT JOIN sources s ON s.id=so.source_id
             WHERE cfs.hackathon_id=:id AND d.active=true
               AND so.normalized_value_json IS NOT NULL
               AND jsonb_typeof(so.normalized_value_json)=
                   CASE d.data_type
                       WHEN 'text' THEN 'string'
                       WHEN 'number' THEN 'number'
                       WHEN 'boolean' THEN 'boolean'
                   END
             ORDER BY d.category,d.label LIMIT 100
        """),
            {"id": hackathon_id},
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]
