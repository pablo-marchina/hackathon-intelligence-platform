# W1 execution status

Implementation date: 2026-09-22
Latest gate update: 2026-09-23 UTC

## Implemented core

- Versioned `SourceAdapter` contract.
- Source policy model (`ALLOWED`, `REVIEW_REQUIRED`, `RESTRICTED`, `PARTNER_REQUIRED`).
- URL canonicalization and deduplicable frontier representation.
- Robots-aware async HTTP fetch engine.
- Per-domain concurrency and request-rate limiting.
- Strict per-source hostname allowlists.
- Conditional request headers (`ETag`, `Last-Modified`).
- Raw-body hashing and S3/MinIO object key strategy.
- SQL repositories for source registry, crawl runs, frontier, fetch attempts, immutable raw documents, and source-event references.
- Persistent source runner: `discover → fetch → fetch_attempt → raw_document → parse → source_event_ref → crawl_run stats`.
- Source health derivation.
- Seed registry with 20 source families and safe-by-default permissions.
- Four explicitly authorized API adapters: Hack Club Hackathons, Hack Club Programs, Hack Club Community Events, and Agenda Tech Brasil.
- Regression tests for all four adapter shapes plus persistent-runner lifecycle.

## Real PostgreSQL acceptance evidence

The isolated PostgreSQL 16 validation branch currently contains live-verified evidence for two connectors:

```text
hackclub_programs
  crawl_runs        1
  fetch_attempts    1
  raw_documents     1
  source_event_refs 4
  status            PARTIAL

agenda_tech_brasil
  crawl_runs        1
  fetch_attempts    1
  raw_documents     1
  source_event_refs 1
  status            PARTIAL
```

`PARTIAL` is intentional: the network response was independently verified through an outbound-capable surface and persisted into the validation database, but the same Python process did not perform both operations. This prevents a false-positive acceptance result.

## Remaining exact acceptance gate

Run from one isolated runtime that has **outbound network + secret injection + Python dependencies**:

```bash
DATABASE_URL='...' python scripts/run_live_sources.py \
  --source hackclub_hackathons \
  --source hackclub_programs \
  --source hackclub_community_events \
  --source agenda_tech_brasil \
  --max-pages 5
```

Then verify for each source that the same run created rows in:

```text
crawl_runs → fetch_attempts → raw_documents → source_event_refs
```

The current execution host cannot close this exact gate because it has no outbound DNS and no Docker/Podman/nerdctl/buildah runtime. Railway could not provision another isolated project due the connected free workspace resource limit, and the surfaced Vercel deploy action was unavailable at invocation time. Existing unrelated user projects were intentionally left untouched.

## Safety posture

- Devpost remains discovery-only/restricted; no prohibited scraper was introduced.
- API/RSS routes only skip robots checks when the source itself explicitly publishes that machine-readable interface.
- External event URLs returned by aggregators become new discovery candidates; they are never fetched under the aggregator's permission.
- No database secret is embedded in code, URLs, fixtures, or artifacts.
