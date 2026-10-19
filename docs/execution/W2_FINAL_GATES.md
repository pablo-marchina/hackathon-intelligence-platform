# W2 Final Gates — Series, Dedup Benchmark, Transactional Split/Undo

Validated on 2026-09-23 against the real W1/W2 corpus.

## 1. Event series → editions

The active W2 corpus was scanned for recurring identity after deduplication. A series is created only when the compact normalized name repeats across canonicals with a minimum temporal span greater than 45 days; same-window events remain dedup candidates instead of editions.

Live PostgreSQL result:

- **25 event series** materialized;
- **60 canonical editions** linked;
- **60 assignment-audit rows**;
- **0 same-series pairs within 14 days**;
- minimum observed inter-edition gap: **133.02 days**.

Examples include Bell Hacks, Blu's Hacks, EurekaHacks, Hack the Nest, HSHacks, Milpitas Hacks, Reverie Hacks, Sierra Hacks, TeenHacks LI and Viking Hacks.

Bell Hacks is represented as three distinct editions:

- 2024-02-03 → `2024-02-1`
- 2024-10-19 → `2024-10-2`
- 2025-12-14 → `2025`

This proves that reused names/domains no longer force a duplicate merge.

## 2. Golden dedup benchmark

`evals/dedup/golden_v0_1.json` contains **470 labelled pairs**:

- 260 same-event pairs: 240 auto-merge representation variants plus 20 missing-date review-band variants anchored to real W2 canonicals;
- 210 different-event pairs: 190 distinct real-canonical pairs plus 20 Bell Hacks recurring-edition hard negatives with missing dates.

The gate requires:

- AUTO_MERGE precision > 98%;
- at least 200 AUTO_MERGE predictions;
- 95% Wilson lower confidence bound > 98%.

Result:

- AUTO_MERGE predictions: **240**
- true-positive AUTO_MERGE: **240**
- false-positive AUTO_MERGE: **0**
- review-band predictions: **40** (20 same-event / 20 different-event); independent Actions review-band step also passed
- review-band composition: **20 same-event / 20 different-event**; REVIEW is intentionally a human-decision queue, not an automatic prediction class
- point precision: **100%**
- AUTO_MERGE recall on same-event benchmark pairs: **92.3077%** (240/260); the remaining 20 same-event pairs are intentionally routed to REVIEW
- Wilson lower 95%: **98.424608%**

Independent GitHub Actions evidence:

- workflow: `Hackathon W2 Final Gates`
- run id: `35877825109`
- conclusion: `success`
- Python: 3.11

The benchmark is intentionally a v0.1 regression gate, not a claim of population-wide 100% precision. W3 should expand it with independently labelled cross-source overlap.

## 3. Transactional merge → undo/split → re-merge

The existing real Sierra Hacks 2025 duplicate was used instead of synthetic rows.

Original merged evidence:

- survivor: `ee8e2a73-f6e6-45a3-99a3-a641de155c2c`
- retired: `350026a8-79d6-4c9a-87a9-1b6f9077b3ac`
- retired source ref: `ba718b61-516c-4a7a-ab88-743a9379a1fe`
- source observations in that group: **14**

Round-trip proof:

1. reconstruct legacy move snapshot;
2. undo legacy merge → both canonicals active, each with 1 member + 14 observations;
3. merge through the new transactional function;
4. undo the new merge → exact 1 member + 14 observations restored to each canonical;
5. preserve alias audit snapshots even after active aliases are removed;
6. re-merge to the correct final canonical state.

During the first native undo test, the gate exposed an `ON DELETE RESTRICT` bug between `merge_alias_changes` and `hackathon_aliases`. The model was corrected so alias audit rows keep `alias_type`/`alias_value` snapshots while `alias_id` can become null via `ON DELETE SET NULL`.

Final merge audit id after the explicit split/re-merge proof: `18741a04-094c-4b8d-afda-79bfef47de98`.

Final canonical state:

- survivor active: yes;
- retired active: no;
- survivor members: 2;
- survivor observations: 28;
- W2-v2 members on inactive canonicals: 0;
- W2-v2 observations on inactive canonicals: 0;
- active W2-v2 canonicals: 250.

## 4. Core implementation

The repository now contains:

- `hackathon_intelligence.series` for edition-safe series inference;
- `hackathon_operations.dedup_eval` with Wilson-bound precision evaluation;
- `evals/dedup/golden_v0_1.json` and reproducible generator;
- Alembic/SQL migration `0002_w2_entity_resolution_hardening`;
- `assign_hackathon_series()`;
- `merge_hackathons_transactional()`;
- `undo_hackathon_merge()`;
- `split_hackathon_merge()`;
- append-only series assignment audit;
- move snapshots for source refs and source observations;
- alias-change audit that survives undo;
- automatic `dedup_candidates(decision=REVIEW)` → `review_queue_items` trigger, verified live with a controlled review-band candidate;
- indexed candidate blocking over external IDs, semantic URLs, title keys/prefixes, organizer/date and location/date buckets (no production all-pairs scan);
- duplicate score includes title, organizer, dates, location, URL-graph overlap and textual similarity.

## Gate conclusion

Final validation: **66/66 tests**, unified eval PASS, repository verifier **59 SQL tables / 5 extensions**, compileall PASS.

Manual series correction was also exercised on Bell Hacks and restored to the inferred value, with audit IDs `e731843d-ef98-458d-b729-6fde22f4889e` (`MANUAL`) and `dcec91ee-828b-4cd1-80a9-596ee4e32799` (`CORRECTION`).

The four requested W2 gates are closed:

- recurring series are materialized as series + distinct editions;
- a versioned golden same-event/different-event benchmark exists;
- the >98% AUTO_MERGE precision gate passes with statistically meaningful support;
- merge/split/undo is transactionally reversible and was exercised against real Sierra Hacks evidence.
