# Typed hackathon attributes — Sprint 2, first vertical slice

## Why this builds on PR #5

PR #5 introduces a bounded `AND`/`OR`/`NOT` search API. This patch is
stacked on that branch and enables the first *database-curated* fields without
hardcoding a new column or operator for each source-specific datum.

## Source-of-truth and provenance

**No duplicate attribute-value table was added.**

- `source_observations` stores raw and normalized values with source ref,
  raw document, extraction method, timestamp, and confidence.
- `canonical_field_selections` designates the actively selected observation
  for each field on each canonical hackathon.
- New `attribute_definitions` describes **curated** keys, value types and
  operator eligibility. The registry does not collect data or create a
  canonical value when none has been selected.

Migration `0014_typed_attribute_catalog` seeds only three fields already
emitted from authorized schema.org/Event extraction:
`attrs.language` (text), `attrs.capacity` (number) and
`attrs.source_event_status` (text).

An additional field needs (a) extractor/normalizer evidence, (b) a canonical
selection policy and (c) a reviewed attribute-definition registration.
An arbitrary source field never becomes searchable merely by appearing in
untrusted HTML.

## Public APIs

- `GET /v1/hackathons/filter-fields`: adds enabled attributes in the database
  to the existing static list, with type, operators and description.
- `POST /v1/hackathons/query`: accepts `attrs.<key>` anywhere in the nested
  expression; performs syntactic validation before DB access and authorization
  against the migrated registry before executing the search.
- `GET /v1/hackathons/{slug}`: adds an `attributes` array of selected
  attributes with value, label, type, observed time, confidence and the
  source URL/name when available. Does not expose unselected conflicting facts.

Example:

```json
{
  "expression": {
    "and": [
      {"field": "attrs.language", "op": "eq", "value": "pt"},
      {"field": "attrs.capacity", "op": "gte", "value": 250},
      {"field": "registration_close_at", "op": "is_known"}
    ]
  }
}
```

## Safety and semantics

- **Curated registry only:** unsupported/inactive/unregistered fields fail.
- **SQL parameters:** field keys and values are bound parameters; no user SQL
  identifiers, JSON path, casts or SQL fragments enter the query.
- **Type checks:** `jsonb_typeof` ensures an incorrectly typed selected
  value is unknown, not cast to the wrong type.
- **NULL-aware:** missing, unselected or mismatched values become SQL NULL.
  `is_unknown` includes those values. `NOT (equals)` does not silently
  include unknowns.
- **Cost bound:** the existing depth, node, branch and `in` limits still apply.
- **Migration:** readiness requires revision `0014_typed_attribute_catalog`;
  deploy migration before the new API version.
- **Legal gates:** no change to source permissions, robots policy or scheduler.

## Acceptance evidence

`python -m pytest -q apps/api/tests/test_typed_attributes.py` is the
offline security/type contract.

`APP_ENV=test HIP_ALLOW_ATTRIBUTES_DB_DRILL=1 python -m
scripts.ops.attribute_catalog_integration_check` uses the migrated
PostgreSQL database and leaves no persistent fixture records. This CI drill
validates type-backed SQL queries, selected provenance, wrong-type values,
comparison, and unknown semantics. The workflow runs it after the existing
migration downgrade/upgrade cycle.

## Next sprints

1. Expand curated definitions for high-value participant and award details;
   add explicit normalized projections where needed.
2. Implement field completeness, confidence/freshness faceting and query
   performance benchmarks.
3. Add frontend controls for nested logical groups and source provenance.
4. Improve controlled source onboarding and the global count of genuinely
   unique, verified hackathons.

This vertical slice is not completion of the worldwide source-coverage plan,
nor permission to activate restricted domains or production collection.
