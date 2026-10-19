# W2 — Extraction, Canonicalization & Deduplication

## Status

W2 is **fully closed** against the real W1 corpus: **15/15 issues / 99/99 points Done** in the Notion backlog.

## Live canonicalization baseline

Official W2 run `1b3e2b67-b1a7-4535-a4f0-291b17eb87f6`:

- 605 real source-event refs consumed;
- 7,346 source observations;
- 251 hackathon-like candidates;
- 354 adjacent/non-hackathon records rejected from the canonical layer;
- 250 active canonical hackathons after dedup;
- 2,277 canonical field selections;
- semantic URL conflict resolution retained in audit history.

## Final entity-resolution gates

- 25 recurring `event_series` materialized from the live corpus;
- 60 editions linked with edition year/label and assignment audit;
- no inferred series pair occurs within the 14-day same-event window;
- golden dedup benchmark: 470 labelled pairs;
- 240/240 AUTO_MERGE decisions correct, 0 false positives;
- review band reported separately: 40 predictions = 20 same-event + 20 different-event;
- AUTO_MERGE precision: 100%;
- 95% Wilson lower bound: 98.424608% (>98% gate);
- independent GitHub Actions run `35877825109`: success (auto-merge precision + separate review-band metrics);
- transactional merge/undo/re-merge exercised on the real Sierra Hacks duplicate;
- explicit `split_hackathon_merge()` proof completed; final re-merge audit `18741a04-094c-4b8d-afda-79bfef47de98`;
- Bell Hacks manual series correction + restoration audited via `e731843d-ef98-458d-b729-6fde22f4889e` and `dcec91ee-828b-4cd1-80a9-596ee4e32799`;
- exact source-member and observation restoration verified;
- alias-audit deletion bug found by the gate and fixed;
- final W2-v2 inactive canonicals retain 0 members and 0 observations;
- REVIEW-band dedup candidate auto-enqueues an OPEN human-review item; live trigger verified and test row cleaned;
- candidate generation uses blocking indexes rather than an all-pairs production scan;
- scorer includes URL-graph overlap and textual-similarity signals alongside title, organizer, date and location.
- final repository validation: 66/66 tests, 59 SQL tables, 5 PostgreSQL extensions, compileall PASS.

See `docs/execution/W2_LIVE_GATE.md` and `docs/execution/W2_FINAL_GATES.md`.

## Remaining W2-adjacent improvements

These no longer block the entity-resolution gate, but remain useful product/data-quality work:

- production LLM fallback with strict structured-output evals;
- broader taxonomy aliases and multilingual normalization;
- canonical change-history replay across multiple real document versions;
- larger independently human-labelled cross-source extraction/dedup datasets as W3 expands coverage.


## Extraction and reconciliation closure

- deterministic structured extraction is evidence-first and null-safe;
- provider-agnostic LLM fallback only receives missing fields and enforces strict Pydantic output + mandatory evidence;
- taxonomy mappings preserve source labels and versioned confidence/method metadata;
- extraction golden v0.1: 8 examples / 43 assertions / 3 languages / structured + JSON-LD, 100% precision/recall/exact-match;
- field-family-specific reconciliation gives official rules priority for eligibility/deadline/prize facts;
- protected conflict cases cover deadline, prize, eligibility and location and persist with all observation IDs;
- canonical change history classifies deadline/prize/status/venue/eligibility/generic edits and suppresses semantically equivalent formatting-only changes.

Final validation: **66/66 tests**, unified eval PASS, **59 SQL tables / 5 extensions**, compileall PASS. GitHub Actions final-gates run: `35877825109` (success).
