# Dedup golden dataset v0.1

This benchmark is anchored to real canonical events produced by the W2 live run on 2026-09-23.

- **260 same-event pairs**: 240 auto-merge representation variants (20 real event anchors × 12 variants) plus 20 missing-date same-event variants deliberately routed to the review band.
- **210 different-event pairs**: 190 cross-pairs among distinct real canonical anchors plus 20 Bell Hacks recurring-edition partial-record hard negatives deliberately routed to the review band.
- Total: **470 labelled pairs**.

The benchmark is intentionally precision-first. A passing build requires:

1. auto-merge precision strictly greater than 98%;
2. at least 200 AUTO_MERGE predictions, so a tiny denominator cannot fake a perfect score; and
3. a 95% Wilson lower confidence bound above 98%.

This v0.1 benchmark is not a substitute for independent multi-source human annotation. It is the first defensible regression gate for automatic merges and should be expanded as W3 adds more source overlap.

Review-band metrics are reported separately from AUTO_MERGE metrics. Threshold changes are covered by the unified eval suite, and any false AUTO_MERGE is a critical regression.
