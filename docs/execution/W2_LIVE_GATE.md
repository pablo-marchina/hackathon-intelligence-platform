# W2 Live Canonicalization Gate — 2026-09-23

## Scope

This gate consumes the 605 real `source_event_refs` produced by the W1 four-source live run. No fixture events are used as W2 input.

Pipeline:

`source_event_refs → extraction → source_observations → normalization → hackathon-like classification → provisional hackathons → dedup candidates → reconciliation → canonical_field_selections → canonical hackathons`

Official live W2 pipeline run:

- pipeline run: `1b3e2b67-b1a7-4535-a4f0-291b17eb87f6`
- GitHub Actions run: `35870359513`
- Python: 3.11
- result: `SUCCEEDED`

## Funnel

| Stage | Count |
|---|---:|
| W1 source-event references | 605 |
| Extracted/replayed references | 605 |
| Field-level source observations | 7,346 |
| Hackathon-like references accepted | 251 |
| Adjacent/non-hackathon references rejected | 354 |
| Provisional hackathons | 251 |
| Automatic merges | 1 |
| Active canonical hackathons | 250 |
| Open dedup review candidates | 0 |
| Detected field conflicts | 1 |
| Open field conflicts after semantic review | 0 |

Accepted by source:

- `hackclub_hackathons`: 250 / 250
- `agenda_tech_brasil`: 1 / 82 (`Hackathon CODA.CE`)
- `hackclub_programs`: 0 / 266
- `hackclub_community_events`: 0 / 7

The 354 rejected records are retained as source evidence/audit records; they are not promoted to the canonical hackathon knowledge base.

## Dedup audit

Only two pairs crossed the persisted candidate threshold:

1. `Sierra Hacks 2025` vs `Sierra Hacks 2025` — score `1.0`, `AUTO_MERGE`. Dates and location are identical; source URLs differ only by the `www` host alias.
2. `Cove Hacks` vs `CSI Hacks` — score `0.777544`, `SEPARATE`.

A real recurring-edition case exposed an important safeguard: two `Bell Hacks` editions reuse `https://bellhacks.tech` while occurring months apart. Exact official URL is therefore not sufficient evidence for an automatic merge. The core dedup policy now requires compatible dates for exact-URL auto-merge:

- same external ID: `0.999`
- same semantic official URL + date distance ≤ 14 days: `0.999`
- same URL + 15–45 days: `0.90` (`REVIEW`)
- same URL + >45 days: `0.70` (`SEPARATE`)
- same URL + missing dates: `0.90` (`REVIEW`)

## Reconciliation / provenance

- 2,277 canonical field selections were persisted.
- Every selected canonical field points back to a field-level source observation.
- One URL conflict was detected for Sierra Hacks: `www.sierrahacks.com` vs `sierrahacks.com`.
- It was resolved as `RESOLVED_EQUIVALENT`; the core now compares URL signatures semantically while preserving the original display URL.

## Canonical data quality

For the 250 active canonicals:

- official URL: 250 / 250
- start date/time: 250 / 250
- end date/time: 250 / 250
- known format: 250 / 250
- city: 194 / 250
- country: 191 / 250
- quality score ≥ 80: 250 / 250
- average quality score: 96.07 / 100
- minimum quality score: 83.33 / 100

Status distribution:

- `COMPLETED`: 231
- `UPCOMING`: 18
- `ONGOING`: 1

Format distribution:

- `IN_PERSON`: 190
- `ONLINE`: 54
- `HYBRID`: 6

The source corpus is currently US-heavy: 134 canonicals are explicitly located in the United States, 25 in Canada, and 1 accepted Agenda Tech event is in Brazil. Fifty-nine canonicals currently lack a normalized country value/code and remain enrichment opportunities.

## Relational integrity

All live audit checks returned zero orphan rows:

- source observation → source event ref
- canonical field selection → selected observation
- canonical member → hackathon
- merge audit → survivor
- merge audit → retired record

## Superseded run

An earlier partial W2 attempt (`e651c9e7-5d1f-4e74-8124-239d1250a80c`) was invalidated after the recurring-edition URL bug was discovered. It is marked `FAILED` with reason `superseded_by_w2_live_v2_edition_safeguard`, and its provisional hackathons were deactivated. It is excluded from every metric above.

## Remaining W2 work

This live gate proves extraction persistence, field-level provenance, normalization, canonical reconciliation, conflict preservation/resolution, candidate generation, scoring and one precision-first auto-merge on real data.

It does **not** yet close:

- explicit `event_series` assignment/linking for recurring editions;
- labeled dedup benchmark large enough to prove the >98% automatic-merge precision target;
- complete reversible split/undo workflow against live PostgreSQL;
- canonical change-history replay across two real versions of the same event;
- broader multilingual taxonomy/eligibility extraction.
