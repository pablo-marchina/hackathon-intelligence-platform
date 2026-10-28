# Scheduled canonical coverage — controlled rollout

## Scope and current guarantee

The opt-in Celery scheduler can now fetch **approved** official APIs with
dedicated event adapters, as well as explicitly authorized RSS/Atom, sitemap
and static-HTML sources. It then normalizes a bounded number of staged
source-event versions into searchable canonical editions during the same
source run. This fixes the previous scheduler path that only persisted
source references and did not canonicalize them.

This change **does not enable** the scheduler, approve a new source, deploy
infrastructure, provision a database or claim production coverage. It
does not convert a generic API seed without a real parser into an adapter.

## Admission gates

A source is schedulable only when all of the following hold:

1. Its **versioned seed** has `permission_status=ALLOWED`,
   `discovery_enabled=true`, `detail_extraction_enabled=true`
   and at least one explicitly scoped seed URL.
2. Its parser is either a registered official-API adapter or one of the
   built-in RSS, sitemap or static-HTML adapters. Generic APIs,
   discovery-only, partner-only and browser modes are excluded.
3. PostgreSQL independently records the source as active and allowed.
   Existing operator pause/revoke overrides are preserved by registry
   refreshes.
4. The source is due, the database-backed lease is active, and all
   existing host, robots, network, rate-limit, permission and commit
   fences remain in force.

A newly discovered domain or external organizer link **does not** inherit
authorization from an aggregator. Source policy review must be independent.

## Safe initial validation

Prepare a **dedicated and migrated staging PostgreSQL**, configured
separately from production. Start with the existing manual
`staging-coverage` GitHub workflow and its protected environment.
Choose `plan` first, then run explicitly approved `apply` campaigns.

The scheduler is disabled by default. In an isolated environment, inspect
its due queue without network access:

```bash
APP_ENV=staging DATABASE_URL="<staging-only DSN>" \
  python -m scripts.ops.run_source_scheduler \
  --limit 1 --max-pages 1 --max-versions 25
```

Only once source permissions, database configuration, staging costs,
monitoring and operational review have been confirmed should the
operator opt in with:

```text
SOURCE_SCHEDULER_ENABLED=true
SOURCE_SCHEDULER_BATCH_SIZE=1
SOURCE_SCHEDULER_MAX_PAGES=1
SOURCE_SCHEDULER_MAX_VERSIONS=25
```

An independently running Celery worker **and** Celery Beat are required.
No new deployment or scheduler process is started by merging this code.

## Behavior and limitations

- The scheduler processes sources sequentially with independent,
  PostgreSQL-clock leases. Fetching is asynchronous; canonical processing
  runs in a worker thread so the lease heartbeat stays responsive.
- Canonical processing commits one version at a time. It checks DB
  permissions under row locks and verifies lease ownership before each
  transaction commits.
- `SUCCEEDED` runs may produce zero new canonicals (all records already
  known). A `PARTIAL` fetch may process previously staged records, but
  the run is still marked unsuccessful for scheduling/backoff.
- `canonical_processed` in the scheduler report counts processed versions,
  **not** unique new hackathons. Use the coverage reports for unique
  canonical counts and distinguish historical from future events.
- Per-cycle version budgets are hard limits, not proof that the canonical
  backlog is drained. Check `/admin/coverage`, backlog and yield reports;
  configure capacity and recrawl intervals based on measured data.
- Sources whose HTTP access, terms or licenses remain unreviewed must stay
  disabled. Do not automatically promote `REVIEW_REQUIRED`, `RESTRICTED`
  or `PARTNER_REQUIRED` sources.

## Acceptance evidence required before production

1. Run `pytest -q packages/operations/tests/test_scheduled_canonical_coverage.py`
   plus the full CI suite.
2. Run controlled staging campaigns on real approved source endpoints and
   archive their redacted receipts.
3. Demonstrate source page → staged version → canonical outcome → search
   on a migrated PostgreSQL database.
4. Verify pause/revoke, lease loss, partial failure and idempotent replay.
5. Track per-source freshness, absolute upcoming events, uniquely attributed
   canonicals, unknown deadlines and backlog.
6. Independently approve security, rights, deployment, operational budgets
   and real user acceptance.

Related backlog: #4 (global coverage), #6 (attributes), #7 (authorized
connectors). These issues remain open after this scheduling increment.
