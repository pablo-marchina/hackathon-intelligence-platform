"""Exact, cursor-independent faceted counts over the advanced-search cohort.

Only a bounded set of safe, curated fields may be aggregated. The query uses
the *same* matching predicates as list search, but intentionally omits LIMIT,
sort, and keyset cursor. It does not infer missing values as False.
"""

from __future__ import annotations

import re
from typing import Any

from hackathon_api.advanced_filters import FilterField
from hackathon_api.attribute_catalog import expression_attribute_keys, load_attribute_fields
from hackathon_api.repositories.hackathons import build_hackathon_predicates
from hackathon_api.schemas import AdvancedHackathonFilters
from sqlalchemy import Connection, text

MAX_FACET_FIELDS = 4
MAX_FACET_BUCKETS = 15

# Expression strings in here are internal, constant SQL, never user SQL.
SCALAR_BUCKETS: dict[str, str] = {
    "format": "h.format::text",
    "event_type": "h.event_type::text",
    "status": "h.status::text",
    "country_code": "l.country_code",
    "edition_year": "h.edition_year::text",
    "is_free": "h.is_free::text",
}
RELATION_BUCKETS: dict[str, str] = {
    "track": (
        "SELECT DISTINCT t.slug::text AS value FROM hackathon_tracks ht "
        "JOIN tracks t ON t.id=ht.track_id WHERE ht.hackathon_id=h.id"
    ),
    "technology": (
        "SELECT DISTINCT t.slug::text AS value FROM hackathon_technologies ht "
        "JOIN technologies t ON t.id=ht.technology_id WHERE ht.hackathon_id=h.id"
    ),
    "source_domain": (
        "SELECT DISTINCT s.domain::text AS value FROM source_event_refs sr "
        "JOIN sources s ON s.id=sr.source_id "
        "WHERE sr.hackathon_id=h.id AND sr.active=true"
    ),
}
DEFAULT_FACETS = ("format", "country_code", "event_type", "source_domain")
STATIC_FACETS = frozenset(SCALAR_BUCKETS) | frozenset(RELATION_BUCKETS)


def validate_facet_fields(fields: list[str]) -> list[str]:
    if not 1 <= len(fields) <= MAX_FACET_FIELDS or len(set(fields)) != len(fields):
        raise ValueError("request 1 to 4 different facet fields")
    for field in fields:
        if field not in STATIC_FACETS and not re.fullmatch(r"attrs\.[a-z][a-z0-9_]{0,62}", field):
            raise ValueError("unsupported facet field")
    return fields


def _bucket_sql(field: str, dynamic_fields: dict[str, FilterField], params: dict[str, Any]) -> str:
    if field in SCALAR_BUCKETS:
        return f"SELECT {SCALAR_BUCKETS[field]} AS value"
    if field in RELATION_BUCKETS:
        return RELATION_BUCKETS[field]
    definition = dynamic_fields.get(field)
    if definition is None or definition.attribute_key is None:
        raise ValueError("unknown or non-filterable attribute facet")
    if definition.kind not in {"text", "boolean"}:
        raise ValueError("facets for numeric attributes are not supported")
    params["facet_attribute_path"] = definition.attribute_key
    target_type = "string" if definition.kind == "text" else "boolean"
    return (
        "SELECT DISTINCT so.normalized_value_json #>> '{}' AS value "
        "FROM canonical_field_selections cfs "
        "JOIN source_observations so ON so.id=cfs.selected_observation_id "
        "WHERE cfs.hackathon_id=h.id AND cfs.active=true "
        "AND cfs.field_path=:facet_attribute_path "
        f"AND jsonb_typeof(so.normalized_value_json)='{target_type}'"
    )


class HackathonFacetRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def summarize(
        self,
        filters: AdvancedHackathonFilters,
        *,
        fields: list[str] | None = None,
        bucket_limit: int = 10,
    ) -> dict[str, Any]:
        chosen = validate_facet_fields(list(fields or DEFAULT_FACETS))
        if not 1 <= bucket_limit <= MAX_FACET_BUCKETS:
            raise ValueError("bucket_limit must be in 1..15")
        if filters.cursor is not None:
            raise ValueError("facet requests may not include a pagination cursor")

        attribute_keys = expression_attribute_keys(filters.expression)
        attribute_keys.update(name[6:] for name in chosen if name.startswith("attrs."))
        definitions = load_attribute_fields(self.connection, attribute_keys)
        joins, predicates, params = build_hackathon_predicates(filters, dynamic_fields=definitions)
        matched = (
            "WITH matched AS (SELECT DISTINCT h.id FROM hackathons h "
            + " ".join(joins)
            + " WHERE "
            + " AND ".join(predicates)
            + ") "
        )
        total = self.connection.execute(
            text(matched + "SELECT COUNT(*) FROM matched"), params
        ).scalar_one()
        result: dict[str, list[dict[str, Any]]] = {}
        for field in chosen:
            bucket_params = {**params, "facet_limit": bucket_limit}
            bucket = _bucket_sql(field, definitions, bucket_params)
            statement = (
                matched
                + "SELECT b.value,COUNT(DISTINCT matched.id) AS count "
                + "FROM matched JOIN hackathons h ON h.id=matched.id "
                + "LEFT JOIN locations l ON l.id=h.primary_location_id "
                + "LEFT JOIN LATERAL ("
                + bucket
                + ") b ON true "
                + "GROUP BY b.value ORDER BY COUNT(DISTINCT matched.id) DESC,"
                + "b.value ASC NULLS LAST LIMIT :facet_limit"
            )
            rows = self.connection.execute(text(statement), bucket_params).mappings().all()
            result[field] = [{"value": row["value"], "count": int(row["count"])} for row in rows]
        return {
            "schema_version": "hackathon-facets-v1",
            "matched_total": int(total),
            "facets": result,
            "bucket_limit": bucket_limit,
            "counts_scope": "ALL_MATCHES_NOT_CURRENT_PAGE",
            "missing_value": (
                "null represents unknown/absent canonical information, "
                "not false and not proof that the publisher omitted the field"
            ),
        }
