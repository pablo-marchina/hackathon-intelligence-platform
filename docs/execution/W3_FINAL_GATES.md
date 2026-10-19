# W3 Final Gates — Open-web Discovery

Validated on 2026-09-23 after W2 entity resolution was closed.

## Scope

W3 closes all six roadmap issues (ISS-39 through ISS-44), totaling **39/39 points**. The implementation intentionally separates discovery evidence from canonical evidence: search snippets and discovery metadata can create candidates but are persisted as `DISCOVERY_ONLY` and cannot silently outrank official/event evidence.

## 1. Multilingual, versioned query generation

`w3-query-v1` supports ten priority languages: English, Portuguese, Spanish, French, German, Italian, Hindi, Japanese, Korean and Chinese. Templates combine localized event types (hackathon, buildathon, datathon, game jam, innovation/AI/coding/security challenges) with registration/deadline/prize/application/team-build signals.

Time windows are generated dynamically from the execution date: current year, next year, current quarter and next quarter. Each generated query has a stable attribution key and is persistable through `discovery_query_templates` + `discovery_queries`.

## 2. Search-result ingestion and provenance

Search-provider results are normalized into a policy-aware record containing provider, query attribution, rank, URL, canonical URL, domain, title/snippet and discovery timestamp. Before a detail fetch, results are classified as:

- `NEW_CANDIDATE`;
- `KNOWN_CANONICAL`;
- `ALREADY_IN_FRONTIER`;
- `POLICY_BLOCKED`.

Search snippets are always marked `DISCOVERY_ONLY`. PostgreSQL enforces this with a CHECK constraint. Existing canonical/frontier URLs therefore do not re-enter the system as new event candidates.

## 3. Sitemap / RSS / Atom discovery

The discovery layer parses robots-advertised `Sitemap:` declarations, bounded common sitemap locations, sitemap indexes and URL sets. RSS and Atom items normalize into the same URL-candidate model. Per-domain limits prevent runaway expansion.

The database persists domain budgets (`max_depth`, `max_pages`, `max_external_domains`) with CHECK constraints.

## 4. Related-source exploration

Related-link classification recognizes rules/terms, registration/apply, results/winners, projects, FAQ, schedule, organizer and challenge routes. The crawl budget defaults to:

- max depth: **2**;
- max pages/domain: **40**;
- max external domains: **12**.

Same-site event-relevant routes are prioritized, while external related domains retain parent/provenance metadata and consume a separate external-domain budget.

## 5. Hackathon-like classifier and golden set

Classifier version: `w3-rule-precision-v2`.

Persisted output includes:

- `is_event`;
- `is_hackathon_like`;
- `event_type`;
- `confidence`;
- score/reasons;
- negative class;
- classifier version.

`evals/discovery/golden_v0_2.json` contains **80 labeled cases across 10 languages and 76 domains**:

- 40 positive hackathon-like pages;
- 40 negative/hard-negative pages.

Hard negatives include innovation challenges/competitions lacking team/build/prototype evidence, articles, guides, winner/result profiles, jobs, courses, conferences and generic landing pages.

Current golden result:

- TP: **40**
- FP: **0**
- TN: **40**
- FN: **0**
- precision: **1.000**
- recall: **1.000**

This is a regression-set result, not a claim of population-wide perfect classification. The automatic-ingestion acceptance metric remains precision-first (>95%).

## 6. Live open-web gate

Independent GitHub Actions evidence:

- workflow: `Hackathon W3 Live Discovery Gate`;
- run id: **35884553285**;
- conclusion: **success**;
- Python: **3.11**;
- targets: **4** public hackathon/event sites;
- policy decisions resolved: **4/4**;
- robots responses available: **2**;
- fail-closed policy decisions: **2**;
- detail pages fetched: **2**;
- fetched pages classified hackathon-like: **2**;
- related links found: **2**;
- sitemap candidates found: **2**.

Two hosts did not provide usable robots policy to the runner. They were **not fetched**; the gate records `FAIL_CLOSED`. This is the intended behavior, not a discovery failure.

## 7. PostgreSQL 17 behavioral gate

The sandbox from previous live gates does not contain the complete W0 schema, so the production migration was not forced onto an incompatible `public` schema. Instead, an isolated PostgreSQL schema was created with minimal dependency stubs, then the **exact `0003_w3_open_web_discovery.sql` migration** was applied.

Behavioral proof:

- 1 versioned query template persisted;
- 1 generated query + attribution persisted;
- 1 search result persisted as `DISCOVERY_ONLY`;
- 1 URL-frontier canonical row created;
- 1 versioned accepted page classification persisted;
- 1 controlled human false-positive automatically created 1 `DISCOVERY_FALSE_POSITIVE` review item;
- repeating the same canonical URL kept **1 frontier row**, raised priority **80 → 95**, and merged metadata.

The isolated validation schema is disposable and is removed after the gate.

## 8. Final repository validation

```text
69 / 69 pytest tests PASS
repository verification: 66 SQL tables / 5 extensions
unified eval: PASS
discovery golden: 80 examples / 10 languages / 76 domains
discovery golden confusion matrix: 40 TP / 0 FP / 40 TN / 0 FN
compileall: PASS
```

## Gate conclusion

W3 is closed: **6/6 issues, 39/39 points Done**. The next execution wave is W4 — API + search + frontend.
