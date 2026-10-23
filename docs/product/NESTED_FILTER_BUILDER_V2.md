# Nested advanced search and country eligibility correctness

## What users can now express

The `/advanced-search` interface is no longer limited to a single flat
`AND` or `OR`. Users can build two visual levels of nested groups with
independent `AND`/`OR` and optional `NOT`, plus `NOT` per condition.

The UI draws its field names, allowed operators and value types from
`GET /v1/hackathons/filter-fields`. This includes reviewed database-backed
`attrs.<key>` attributes introduced in PR #8.

Example:

```json
{
  "limit": 30,
  "sort": "deadline",
  "expression": {
    "and": [
      {"field": "format", "op": "eq", "value": "ONLINE"},
      {
        "or": [
          {"field": "attrs.language", "op": "eq", "value": "pt"},
          {"not": {"field": "eligible_country", "op": "eq", "value": "BR"}}
        ]
      }
    ]
  }
}
```

Editor actions use immutable tree updates with stable IDs. Removing a nested
group never removes other conditions. Empty groups cannot be submitted. The
client enforces the existing server AST depth (5), node budget (32), and
per-group child budget (12) before POST, while the server remains the
authoritative validator.

## Correctness fix: eligible_country

An explicit `eligibility_countries.allowed=false` row must **not** match an
`eligible_country` filter. The SQL now requires `ec.allowed=true` in each
positive relation. `eligible_worldwide=true` remains a separate documented
alternative.

Presence semantics now handle SQL NULL explicitly:
- `is_known` is true for an explicit allowed-country record or
  `eligible_worldwide=true`.
- `is_unknown` is true if neither positive record nor worldwide eligibility
  is known; the NULL worldwide flag must not make `NOT (UNKNOWN)` turn into
  SQL NULL and disappear from the results.
- `NOT eligible_country=BR` still respects SQL's three-valued logic: an
  event with no reported worldwide status and no country rules is *unknown*,
  not *known not eligible*.

The existing column and index on `eligibility_countries(country_code,allowed,hackathon_id)`
are reused. This PR does not modify source permissions, collect events,
invent restrictions, or change the canonical data model.

## Verification

Frontend with no browser/network dependency:

```bash
cd apps/web
npm run test:filters
npm run typecheck
npm run lint
npm run build
```

The native Node.js tests cover nested AND/OR/NOT ASTs, immutable editing,
single-condition serialization, empty groups and complexity bounds.

PostgreSQL integration in CI, with disposable fixtures and rollback:

```bash
APP_ENV=test HIP_ALLOW_ELIGIBILITY_DRILL=1 \
DATABASE_URL="<disposable local PostgreSQL>" \
python -m scripts.ops.country_eligibility_integration_check
```

Four synthetic canonical hackathons verify explicit allow, explicit deny,
worldwide allow, and wholly unknown eligibility. No external API is called.

## Known remaining work

The editor supports up to two explicit nested group levels, which covers
common boolean queries but not every possible AST of the backend.
Full global hackathon coverage still requires additional *authorized* live
source connectors and measured staging ingestion. This is a filter interface
and semantics improvement, not an increase in harvested hackathons.
