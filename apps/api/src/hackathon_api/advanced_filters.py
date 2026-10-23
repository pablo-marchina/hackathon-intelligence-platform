"""Bounded, allowlisted SQL predicates for composable hackathon search.

Never interpolate client-provided SQL, paths, operators or values into SQL text.
Only registered field definitions may be queried. Missing scalar data is SQL
NULL: negative comparisons do not silently promote unknown values to False.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

MAX_DEPTH = 5
MAX_NODES = 32
MAX_BRANCHES = 12
MAX_SET_SIZE = 20

SCALAR_OPERATORS = ("eq", "ne", "in", "is_known", "is_unknown")
NUMBER_OPERATORS = (*SCALAR_OPERATORS, "gt", "gte", "lt", "lte", "between")
DATE_OPERATORS = (*SCALAR_OPERATORS, "gt", "gte", "lt", "lte", "between")
TEXT_OPERATORS = (*SCALAR_OPERATORS, "contains")
BOOLEAN_OPERATORS = ("eq", "ne", "is_known", "is_unknown")
RELATION_OPERATORS = ("eq", "in", "is_known", "is_unknown")


@dataclass(frozen=True)
class FilterField:
    kind: str
    operators: tuple[str, ...]
    column: str | None = None
    relation_from: str | None = None
    relation_value: str | None = None
    description: str = ""


FIELDS: dict[str, FilterField] = {
    "name": FilterField("text", TEXT_OPERATORS, "h.name"),
    "event_type": FilterField("text", SCALAR_OPERATORS, "h.event_type::text"),
    "status": FilterField("text", SCALAR_OPERATORS, "h.status::text"),
    "format": FilterField("text", SCALAR_OPERATORS, "h.format::text"),
    "city": FilterField("text", TEXT_OPERATORS, "l.city"),
    "country_code": FilterField("country", SCALAR_OPERATORS, "l.country_code"),
    "start_at": FilterField("datetime", DATE_OPERATORS, "h.start_at"),
    "end_at": FilterField("datetime", DATE_OPERATORS, "h.end_at"),
    "registration_open_at": FilterField("datetime", DATE_OPERATORS, "h.registration_open_at"),
    "registration_close_at": FilterField("datetime", DATE_OPERATORS, "h.registration_close_at"),
    "last_verified_at": FilterField("datetime", DATE_OPERATORS, "h.last_verified_at"),
    "edition_year": FilterField("number", NUMBER_OPERATORS, "h.edition_year"),
    "participation_fee": FilterField("number", NUMBER_OPERATORS, "h.participation_fee"),
    "quality_score": FilterField("number", NUMBER_OPERATORS, "h.quality_score"),
    "source_count": FilterField("number", NUMBER_OPERATORS, "h.source_count"),
    "is_free": FilterField("boolean", BOOLEAN_OPERATORS, "h.is_free"),
    "eligible_worldwide": FilterField("boolean", BOOLEAN_OPERATORS, "h.eligible_worldwide"),
    "minimum_age": FilterField(
        "number",
        NUMBER_OPERATORS,
        "(SELECT er.minimum_age FROM eligibility_rules er WHERE er.hackathon_id=h.id)",
    ),
    "maximum_age": FilterField(
        "number",
        NUMBER_OPERATORS,
        "(SELECT er.maximum_age FROM eligibility_rules er WHERE er.hackathon_id=h.id)",
    ),
    "minimum_team_size": FilterField(
        "number",
        NUMBER_OPERATORS,
        "(SELECT er.minimum_team_size FROM eligibility_rules er WHERE er.hackathon_id=h.id)",
    ),
    "maximum_team_size": FilterField(
        "number",
        NUMBER_OPERATORS,
        "(SELECT er.maximum_team_size FROM eligibility_rules er WHERE er.hackathon_id=h.id)",
    ),
    "student_required": FilterField(
        "boolean",
        BOOLEAN_OPERATORS,
        "(SELECT er.student_required FROM eligibility_rules er WHERE er.hackathon_id=h.id)",
    ),
    "individual_allowed": FilterField(
        "boolean",
        BOOLEAN_OPERATORS,
        "(SELECT er.individual_allowed FROM eligibility_rules er WHERE er.hackathon_id=h.id)",
    ),
    "prize_pool_usd": FilterField(
        "number",
        NUMBER_OPERATORS,
        "(SELECT MAX(pp.normalized_usd_amount) FROM prize_pools pp WHERE pp.hackathon_id=h.id)",
        description="Largest reported prize pool, not a sum of awards.",
    ),
    "track": FilterField(
        "text",
        RELATION_OPERATORS,
        relation_from="hackathon_tracks ht JOIN tracks t ON t.id=ht.track_id",
        relation_value="t.slug",
    ),
    "technology": FilterField(
        "text",
        RELATION_OPERATORS,
        relation_from="hackathon_technologies hx JOIN technologies tx ON tx.id=hx.technology_id",
        relation_value="tx.slug",
    ),
    "organizer": FilterField(
        "text",
        RELATION_OPERATORS,
        relation_from="hackathon_organizations ho JOIN organizations o ON o.id=ho.organization_id",
        relation_value="o.slug",
        description="Official organizer/co-organizer/host slug.",
    ),
    "sponsor": FilterField(
        "text",
        RELATION_OPERATORS,
        relation_from="hackathon_organizations ho JOIN organizations o ON o.id=ho.organization_id",
        relation_value="o.slug",
        description="Sponsor or prize/technology partner slug.",
    ),
    "source_domain": FilterField(
        "text",
        RELATION_OPERATORS,
        relation_from="source_event_refs sr JOIN sources s ON s.id=sr.source_id",
        relation_value="s.domain",
    ),
    "eligible_country": FilterField(
        "country",
        RELATION_OPERATORS,
        relation_from="eligibility_countries ec",
        relation_value="ec.country_code",
        description="Explicitly allowed countries; worldwide eligibility is stored separately.",
    ),
}


def filter_field_catalog() -> dict[str, object]:
    return {
        "schema_version": "advanced-filter-v1",
        "max_depth": MAX_DEPTH,
        "max_nodes": MAX_NODES,
        "fields": [
            {
                "key": name,
                "type": spec.kind,
                "operators": list(spec.operators),
                "description": spec.description,
            }
            for name, spec in FIELDS.items()
        ],
    }


def _typed_value(field: FilterField, value: Any) -> Any:
    kind = field.kind
    if kind == "boolean":
        if type(value) is not bool:
            raise ValueError("boolean filter values must be true or false")
        return value
    if kind == "number":
        if type(value) not in (int, float) or abs(value) > 1e12 or not math.isfinite(value):
            raise ValueError("number filter must be finite and within supported bounds")
        return value
    if not isinstance(value, str) or not 1 <= len(value) <= 200:
        raise ValueError("filter text must contain 1 to 200 characters")
    if kind == "country":
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", value):
            raise ValueError("country must be a two-letter ISO country code")
    elif kind == "datetime":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("date filter requires an ISO 8601 datetime") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("date filter requires an explicit timezone offset")
        return parsed
    return value


def validate_expression(expression: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate every node before database access, including complete query complexity."""
    if expression is None:
        return None
    count = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("filter expression exceeds complexity limits")
        if not isinstance(node, dict):
            raise ValueError("filter expression nodes must be objects")
        keys = set(node)
        if keys == {"not"}:
            walk(node["not"], depth + 1)
        elif keys == {"and"} or keys == {"or"}:
            operation = next(iter(keys))
            children = node[operation]
            if not isinstance(children, list) or not 1 <= len(children) <= MAX_BRANCHES:
                raise ValueError("logical groups must contain 1 to 12 conditions")
            for child in children:
                walk(child, depth + 1)
        elif keys in ({"field", "op", "value"}, {"field", "op"}):
            name, op = node.get("field"), node.get("op")
            if not isinstance(name, str) or name not in FIELDS:
                raise ValueError("unknown or non-filterable field")
            field = FIELDS[name]
            if not isinstance(op, str) or op not in field.operators:
                raise ValueError("unsupported operator for field")
            if op in ("is_known", "is_unknown"):
                if "value" in node:
                    raise ValueError("presence operators must omit value")
            elif "value" not in node:
                raise ValueError("filter operator requires value")
            elif op == "in":
                values = node["value"]
                if not isinstance(values, list) or not 1 <= len(values) <= MAX_SET_SIZE:
                    raise ValueError("in operator requires 1 to 20 values")
                for value in values:
                    _typed_value(field, value)
            elif op == "between":
                values = node["value"]
                if not isinstance(values, list) or len(values) != 2:
                    raise ValueError("between requires two values")
                lower, upper = (_typed_value(field, v) for v in values)
                if lower > upper:
                    raise ValueError("between range must be ordered")
            else:
                _typed_value(field, node["value"])
        else:
            raise ValueError("invalid filter expression structure")

    walk(expression, 0)
    return expression


def compile_expression(expression: dict[str, Any], params: dict[str, Any]) -> str:
    """Compile a validated expression to static SQL fragments plus bound params."""
    validate_expression(expression)
    index = 0

    def bound(value: Any) -> str:
        nonlocal index
        name = f"advanced_{index}"
        index += 1
        params[name] = value
        return ":" + name

    def compile_node(node: dict[str, Any]) -> str:
        if "and" in node:
            return "(" + " AND ".join(compile_node(child) for child in node["and"]) + ")"
        if "or" in node:
            return "(" + " OR ".join(compile_node(child) for child in node["or"]) + ")"
        if "not" in node:
            return "(NOT " + compile_node(node["not"]) + ")"
        field = FIELDS[node["field"]]
        op = node["op"]
        if field.relation_from is not None:
            assert field.relation_value is not None
            alias = field.relation_from.split(" ")[1]
            identity = f"{alias}.hackathon_id=h.id"
            if node["field"] == "organizer":
                identity += " AND ho.role::text IN ('ORGANIZER','CO_ORGANIZER','HOST')"
            elif node["field"] == "sponsor":
                identity += " AND ho.role::text IN ('SPONSOR','PRIZE_PARTNER','TECHNOLOGY_PARTNER')"
            if op in ("is_known", "is_unknown"):
                exists = f"EXISTS (SELECT 1 FROM {field.relation_from} WHERE {identity})"
                if node["field"] == "eligible_country":
                    exists = f"(h.eligible_worldwide=true OR {exists})"
                return exists if op == "is_known" else "(NOT " + exists + ")"
            values = node["value"] if op == "in" else [node["value"]]
            values = [_typed_value(field, item) for item in values]
            if op == "eq":
                predicate = f"{field.relation_value}={bound(values[0])}"
            else:
                predicate = f"{field.relation_value}=ANY({bound(values)})"
            exists = (
                f"EXISTS (SELECT 1 FROM {field.relation_from} WHERE {identity} AND {predicate})"
            )
            if node["field"] == "eligible_country":
                return f"(h.eligible_worldwide=true OR {exists})"
            return exists
        assert field.column is not None
        column = field.column
        if op == "is_known":
            return f"({column} IS NOT NULL)"
        if op == "is_unknown":
            return f"({column} IS NULL)"
        if op == "in":
            values = [_typed_value(field, item) for item in node["value"]]
            return f"({column}=ANY({bound(values)}))"
        if op == "between":
            lower, upper = (_typed_value(field, value) for value in node["value"])
            return f"({column} BETWEEN {bound(lower)} AND {bound(upper)})"
        value = _typed_value(field, node["value"])
        if op == "contains":
            return f"(POSITION(LOWER({bound(value)}) IN LOWER({column}))>0)"
        sql_op = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
        return f"({column}{sql_op}{bound(value)})"

    return compile_node(expression)
