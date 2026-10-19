# W4 final gates — API + search + frontend

W4 closes the public/read-product layer on top of the W2 canonical entity-resolution layer and the W3 discovery layer.

## Final acceptance baseline

- 9/9 W4 issues complete.
- 63/63 W4 points complete.
- 83/83 local pytest tests pass.
- Repository verifier: 69 PostgreSQL tables and 5 required extensions.
- Unified W2/W3 regression eval: PASS.
- Python compileall: PASS.

## Canonical API gate

The versioned `/v1` API exposes canonical-only hackathon listing/detail plus organizations, event series, tracks, technologies and sources. Large collections use deterministic keyset cursors bound to their active sort/filter fingerprint. Responses expose `last_verified_at`, `source_count` and `quality_score` where applicable.

Retired/merged hackathon slugs resolve to the active canonical with HTTP 308 rather than surfacing stale entities.

## P0 filtering and search gate

Structured filters cover lifecycle/registration status, event type, date windows, format, country/city, tracks, technologies, organizer/sponsor, prize floor/ceiling, free-only, team size and basic eligibility. Event location and eligibility geography remain distinct (`country_code` versus `eligible_country`).

Search uses `hackathon_search_documents`, built only for active canonicals and enriched with canonical text, organizations, tracks and technologies. Ranking combines PostgreSQL text relevance, canonical quality and freshness. A full rebuild path exists, and trigger-driven refresh removes inactive/merged canonicals from the search index.

### Production-like PostgreSQL benchmark

Validated on PostgreSQL 17.6 with 50,000 generated production-like canonical rows and matching prize/eligibility/search rows:

- samples: 60
- min: 39.303 ms
- average: 40.532 ms
- p50: 40.232 ms
- **p95: 41.538 ms**
- max: 52.230 ms

A representative combined FTS + status + format + geography + team-size + prize query completed in 96.348 ms under `EXPLAIN (ANALYZE, BUFFERS)` without temporary spill.

Search-index cleanup checks after retiring a canonical:

- retired canonical documents: 0
- inactive canonicals left indexed: 0

## Natural-language parsing gate

The deterministic natural-language parser compiles into the exact same structured-filter schema used by manual filtering. Unsupported constraints are returned explicitly rather than hallucinated. Parsed filters are inspectable and removable before final deterministic execution.

## Authentication and user-state gate

Google and GitHub OAuth use PKCE S256 and signed state. Provider access tokens are not persisted. Provider identities map to internal UUID users; verified-email linking and provider-subject uniqueness avoid duplicate accounts.

Sessions are opaque, persisted only as hashes, with HttpOnly cookies and a separate CSRF token/hash. Privacy boundaries include export and deletion request schema/API hooks.

PostgreSQL validation confirmed idempotent user-event state transitions:

- state rows after SAVED → APPLIED: 1
- final state: APPLIED
- session rows: 1

## Frontend gate

The Next.js frontend consumes `/v1` rather than hard-coded events. Public search state is URL-addressable, responsive, and distinguishes no-results from data-source outage. Canonical detail pages expose the rich event schema, provenance/freshness, recent changes, registration versus canonical URL, SEO metadata and Event JSON-LD. Organization and series routes consume their canonical APIs; library state is authenticated.

The final frontend acceptance used factual events captured from the real W2 corpus, including MoonStone and Bay-Valley Hacks. No synthetic event record was used in the acceptance smoke.

### Final GitHub Actions web gate

Run: `35898513405`

Passed:

- Node/Python setup
- factual W2 snapshot API
- frontend dependency install
- TypeScript typecheck
- optimized `next build`
- production `next start`
- homepage smoke
- structured-search smoke
- canonical detail smoke including eligibility and canonical official URL
- authenticated-library surface smoke

## Regression commands

```bash
PYTHONPATH=$(find packages -mindepth 2 -maxdepth 2 -type d -name src | paste -sd: -):apps/api/src pytest -q
python scripts/verify_repository.py
PYTHONPATH=$(find packages -mindepth 2 -maxdepth 2 -type d -name src | paste -sd: -):apps/api/src python scripts/run_evals.py
python -m compileall -q apps packages scripts
```
