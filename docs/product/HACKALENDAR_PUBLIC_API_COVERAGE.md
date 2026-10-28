# Hackalendar public API — controlled source expansion (2026-10-28)

## Why this source

The public [Hackalendar API](https://hackalendar.com/api) explicitly offers
machine-readable hackathon listings for free, without an API key. Its
[agent documentation](https://hackalendar.com/agents) lists the event
fields and offset-based pagination. Publisher policy asks integrators to
link users to the organiser's registration page. This integration consumes
only the read-only public GET `/api/events`; it does **not** subscribe,
create private access, submit events, use private APIs, scrape
organizer websites or access OpenStreetMap address databases.

The seed is versioned as `hackalendar` with `ALLOWED` API policy,
a 12-hour minimum refresh interval, max 2 requests/minute and
`durable_api_pages=true`. That registry policy is not itself a grant to
operate on any PostgreSQL instance: independent operator-managed DB
registration and approval are required before a fetch.

## Feed contract

- Seed: `https://hackalendar.com/api/events?limit=200&offset=0`.
- Response: `{ "data": [...], "meta": { "hasMore": boolean,
  "nextOffset": number | null } }`.
- Pagination: construct **only** a same-host, same-path HTTPS URL with
  bounded `limit` (1–200) and increasing `offset` (0–100000).
  The persistent API queue limits pagination depth to 100 hops per chain;
  a caller's page budget can end earlier. Never use an arbitrary
  URL supplied by the API as a continuation.
- Each event keeps its source-native `slug` as its stable source ID.
  Missing/duplicate slugs or names are skipped, not guessed from the
  list URL. Raw documents remain independently auditable.
- Known source values are retained: dates and precision, registration
  link, deadline, mode, city/country when explicitly structured,
  themes, fee/free flag, prize text, organiser and cancellation flag.
  The source is a curated hackathon-only index, so the classifier can
  correctly accept publisher-vetted events even if their brand name
  omits the literal word "hackathon".
- For dates with unknown time or timezone, keep `YYYY-MM-DD` or
  `null` as supplied; never invent signup timezones or registration
  windows. The current canonical event-date parser has a separate
  historical UTC-midnight limitation for date-only starts/ends.
- Registration deadlines are projected to the searchable
  `registration_close_at` field **only when a timezone-bearing
  timestamp is explicitly present**. Date-only deadlines stay in
  source metadata, never guessed as UTC. Unknown `isFree` stays
  unknown, not false or zero. Prize text is preserved without
  inventing a USD amount.
- The source-native `cancelled` signal is retained in the original
  payload; separate status reconciliation is required before claiming
  correct cross-source cancellation status.

## Safe rollout

1. Review [the publisher's public data guide](https://hackalendar.com/api)
   and this seed/adapter against current access terms.
2. Merge the prerequisite opt-in scheduler PR #23, then this source PR.
3. Provision a dedicated migrated staging DB and **independently**
   register/approve `hackalendar` in the DB. Do not run against production.
4. Run the manual `staging-coverage-campaign` workflow on `main`
   with profile `hackalendar`, mode `plan`, page budget 1 and
   version budget 25; no network or DB is accessed in plan mode.
5. After reviewing staging credentials, authorization, source access
   and costs, use the protected `apply` workflow. Inspect the
   redacted before/after receipt and whether the durable URL backlog
   remains pending.
6. Expand the page/version budgets gradually; repeat to prove
   idempotence, incremental updates, source attribution, unique
   canonical yield and field completeness. Do not treat a successful
   synthetic test or `ALLOWED` seed as evidence of live coverage.
7. If the source returns schema-incompatible data, pauses or 429s,
   stop and investigate; do not bypass rate limits or policy gates.

## Test coverage

`packages/source_sdk/tests/test_hackalendar_adapter.py` runs offline,
using publisher-documented response shapes. It checks bounded pagination,
host/path restrictions, duplicate stable IDs, preserved date precision,
classification, selected canonical fields and no calls to external
registration links. Standard CI exercises the new adapter as part of
the Python suite; a **real staging campaign remains pending**.

The controlled staging profile `hackalendar` is separate from the
legacy `all` profile (eight historical sources) so the existing
eight-source batch budget is not silently expanded. No
`SOURCE_SCHEDULER_ENABLED` setting is changed.
