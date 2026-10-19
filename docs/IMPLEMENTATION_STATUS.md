# Implementation Status

This repository is a production-oriented implementation baseline for the Global Hackathon Intelligence Platform.

## Verification labels

- **Done:** implementation and acceptance behavior can be verified in the current runtime without external infrastructure.
- **Blocked:** implementation is ready enough to proceed, but an external prerequisite is unavailable.
- **Review:** implementation exists and is waiting on a specific acceptance gate.
- **In progress:** meaningful implementation exists but acceptance scope is not yet complete.
- **Backlog:** not yet implemented.

## Current wave state

- W0 Foundation + PostgreSQL: **11/13 issues Done, 2 Blocked, 0 Review.** The real PostgreSQL validation passed schema upgrade → downgrade → re-upgrade; remaining blockers are container-runtime/CI-infrastructure gates external to this artifact.
- W1 Ingestion + connectors: **4/4 initial authorized adapters passed the live runner gate.** GitHub Actions/Python 3.11 produced real `crawl_runs → fetch_attempts → raw_documents → source_event_refs` for Hack Club Hackathons, Hack Club Programs, Hack Club Community Events, and Agenda Tech Brasil.
- W2 Extraction + canonicalization + dedup: **fully closed — 15/15 issues / 99/99 points Done on the real 605-record W1 corpus.** The pipeline produced 250 active canonicals, materialized 25 recurring series / 60 editions, passed a 470-pair dedup benchmark at 100% point precision with 98.4246% Wilson lower bound, and passed a transactional merge → undo/split → re-merge round trip on real Sierra Hacks evidence.
- W3 Open-web discovery: **fully closed — 6/6 issues / 39/39 points Done.** Versioned multilingual queries, policy-aware search ingestion, robots/sitemap/RSS discovery, bounded related-source exploration and classifier persistence are implemented; the 80-case golden set covers 10 languages / 76 domains at 40 TP / 0 FP / 40 TN / 0 FN, GitHub Actions live-web run `35884553285` passed with fail-closed policy behavior, and the exact W3 migration passed PostgreSQL 17 behavioral validation.
- W4 API + search + frontend: **fully closed — 9/9 issues / 63/63 points Done.** Versioned canonical APIs, complete P0 filtering, active-canonical FTS, deterministic NL filter parsing, OAuth/profile foundation, user states and the Next.js public product are implemented; PostgreSQL 17.6 benchmarked 50,000 events at 41.538 ms p95 over 60 samples, and GitHub Actions run `35898513405` passed typecheck, optimized build, production start and factual W2-data HTTP smokes.
- W5 Recommendation + alerts: **fully closed — 6/6 issues / 42/42 points Done.** Versioned preferences, deterministic tri-state eligibility, reproducible explainable ranking, saved-search fingerprinting, idempotent timezone-aware alert generation, in-app inbox and SMTP delivery are implemented. PostgreSQL 17 behavioral gates passed, and GitHub Actions run `35905927315` passed production Next.js build/smokes plus a real SMTP send into Mailpit.
- W6 Admin + evals + hardening: quality/regression/scheduling/security/eval/DR/load-test tooling exists; 232 local Python tests pass as of iteration 9 (2026-10-15). Database-clock leases, PostgreSQL two-worker contention and isolated Celery/Redis CI checks are implemented. W6 iteration 9 adds ASGI streaming request-size enforcement, version-aware `/ready`, and a PostgreSQL readiness CI check. Production-like runtime drills remain outstanding; do not mark W6 Done.

## W1 authorized connector baseline

The initial connector portfolio deliberately prefers documented/public APIs over HTML scraping:

1. `hackclub_hackathons` — official Hack Club hackathon directory JSON API.
2. `hackclub_programs` — documented Hack Club Programs API; downstream classifier selects hackathon-like programs.
3. `hackclub_community_events` — documented Hack Club community-events JSON API; downstream classifier selects hackathon-like events.
4. `agenda_tech_brasil` — GitHub Contents API over the community-maintained factual event database; ingest factual fields with provenance.

All four are active in the PostgreSQL validation Source Registry with `ingestion_mode=API`, `permission_status=ALLOWED`, and `detail_extraction_enabled=true`. External event URLs emitted by an aggregator are **not** implicitly fetched under that aggregator's permission: the fetcher enforces a per-source hostname allowlist.

## No-mock acceptance principle

The final release gate must use real ingested source data. Unit/evaluation fixtures may preserve real public event facts for deterministic regression testing, but they do not substitute for the final live end-to-end acceptance test.


## W1 live runner evidence

The initial four authorized adapters now have end-to-end runner evidence. See `docs/execution/W1_LIVE_GATE.md` for crawl-run IDs and persisted counts.

