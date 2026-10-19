# W1 Live Persistence Gate

Validation timestamp: 2026-09-23 UTC

## Goal

Prove that authorized source data can enter the persistent acquisition chain:

```text
source → crawl_runs → fetch_attempts → raw_documents → source_event_refs
```

The final acceptance requirement is stricter: the **same outbound-enabled Python process** must execute `scripts/run_live_sources.py` and write the resulting evidence to PostgreSQL for all four initial connectors.

## Runner implementation

`scripts/run_live_sources.py` now defaults to persistence when `DATABASE_URL` is present. It delegates to `run_source_persisted`, which:

1. upserts/loads the source registry row;
2. starts a `crawl_run`;
3. enqueues the source URL in `url_frontier`;
4. records the HTTP attempt;
5. content-hashes and persists a `raw_document`;
6. parses source-native event records;
7. upserts `source_event_refs`;
8. follows only bounded, same-authorized-host pagination;
9. closes the crawl with persisted counts and errors.

## Real PostgreSQL validation evidence

Validation target: isolated PostgreSQL 16 acceptance branch. Credentials are intentionally omitted.

### Hack Club Programs

```text
crawl_run_id      af0fab96-4288-4edd-88ac-c3cc82ec69ec
fetch_attempt_id  1
raw_document_id   2c8e17a9-84cf-49fa-af91-727d84c47fc6
source_event_refs 4
status            PARTIAL
```

The live public API response was network-verified before persistence. The stored sample includes Sticky, Crescent, Atlantis, and Capitol.

### Agenda Tech Brasil

```text
crawl_run_id      bd4c951f-36a4-4009-a7d7-a878875ce602
fetch_attempt_id  2
raw_document_id   ca6d60de-39c3-4b8c-add1-cd969fa182ea
source_event_refs 1
status            PARTIAL
```

The live GitHub Contents response was network-verified before persistence; the observed repository file SHA was `6785d51addd64d28f8739b147e05d41904827e93`.

## Aggregate verification

```text
source_family                 crawl_runs  fetch_attempts  raw_documents  source_event_refs
agenda_tech_brasil            1           1               1              1
hackclub_programs             1           1               1              4
hackclub_hackathons           0           0               0              0
hackclub_community_events     0           0               0              0
```

## Why the two passing rows remain PARTIAL

The evidence is real and persisted, but the source request was performed through an external outbound-capable verification surface and then inserted through the authenticated database channel. It would be incorrect to claim the production Python runner executed both operations in one runtime.

Therefore these rows are marked `PARTIAL` / `PARTIAL_VERIFIED`, and ISS-21 remains `In progress` until all four connectors pass the same-process gate.

## Exact remaining command

On an isolated runtime with Python dependencies, outbound network, and secure `DATABASE_URL` injection:

```bash
python scripts/run_live_sources.py \
  --source hackclub_hackathons \
  --source hackclub_programs \
  --source hackclub_community_events \
  --source agenda_tech_brasil \
  --max-pages 5
```

Then verify that every source has new rows in all four persistent acquisition tables and that the produced crawl run is `SUCCEEDED` or an explicitly understood `PARTIAL` rather than an infrastructure failure.

## Runtime blockers observed

- current execution host: no outbound DNS from Python/container process;
- current execution host: no Docker, Podman, nerdctl, buildah, Finch, Colima, or Lima runtime;
- Railway connected free workspace: additional isolated provisioning unavailable due resource limit;
- surfaced Vercel deployment action: unavailable when invoked;
- Neon PostgreSQL: no `http`, `pg_net`, or `pgsql-http` extension available;
- unrelated existing Railway/Supabase projects were intentionally not modified.
