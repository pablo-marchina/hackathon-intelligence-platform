# Authorized API coverage expansion — Hack Club & Agenda Tech Brasil

The global product goal is *unique canonical hackathons with linked, checkable
fields*, including historical editions, not merely a count of source domains.

This increment stays inside the four **already approved** API sources in the
registry; it does not activate or approve a new domain, scrape organizer links,
or change robots/TOS gates.

## 1. Hack Club Programs: documented partitions

The official [Hack Club Events OpenAPI v1](https://hackclub.com/api/v1/openapi.json)
currently documents these `GET /api/v1/events` query parameters:

- `status=upcoming|ongoing|ended`
- `limit=1..100`

Three explicit seed URLs are registered, each constrained to `hackclub.com`,
with a maximum of 100 events per partition.

The API currently documents **no offset/cursor**. The 100-result ceiling per
status therefore remains a known upper bound; fetching three partitions
increases coverage but does **not** guarantee all historical events.

Items are classified before canonical promotion: Hack Club programs unrelated
to hackathons do not automatically become catalog entries.

The adapter now preserves its published description and source lifecycle
status, which are selected as separately sourced observations. The API's
`format=both` is normalized to `HYBRID`; `in-person` remains `IN_PERSON`.
The source lifecycle status `upcoming` must **not** be interpreted as
`registration_open`, and source URLs are never treated as fetch permissions.

## 2. Agenda Tech Brasil: historical coverage

Archived years or months previously disappeared from the parsed output. The
adapter now retains historical entries, recording `metadata.archived`.

Every event gets a deterministic, edition-scoped source record ID using the
source's year, month, normalized name and official website. This prevents
different events sharing the same calendar API URL/day from overwriting each
other. The calendar does not publish immutable IDs; changes to titles, website
or month can still create a new source ref, which is reconciled through the
existing canonical pipeline and dedup logic.

Bad dates are ignored rather than interpreted as valid dates, and events with
no external organizer website are still preserved with the calendar as the
provenance source. Generic workshops are still excluded by the hackathon
classifier during canonicalization.

## Testing and execution

All parser tests use mocked source payloads, never real HTTP:

```bash
pytest -q packages/source_sdk/tests/test_authorized_api_coverage.py
python -m pytest -q
```

The normal controlled run remains subject to the registry + database
permissions, operator pause, rate limits and explicit staging/development
apply gates. A dry run:

```bash
python -m scripts.ops.controlled_catalog_ingestion \
  --source hackclub_programs --max-pages 3
```

Staging execution, only by a person/automation with authorized credentials:

```bash
APP_ENV=staging HIP_ALLOW_LIVE_INGESTION=1 \
DATABASE_URL="<staging DB URL>" \
python -m scripts.ops.controlled_catalog_ingestion \
  --source hackclub_programs --max-pages 3 --max-versions 300 --apply
```

Never infer live canonical growth from seeded URLs or synthetic test records:
report `CREATED`, `LINKED`, `UPDATED`, `REJECTED` from actual authorized
runs, deduplicate editions, and track unprocessed backlog separately.

## Next gaps

- Source review and actual permission for additional platforms (Issue #7).
- Persistent, resumable pagination/partition state for larger APIs.
- Linking more fields including rules, prize packages and eligibility.
- Global discovery and coverage estimates from independent reference samples.
