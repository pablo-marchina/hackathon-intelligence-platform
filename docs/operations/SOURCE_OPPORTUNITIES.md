# Marginal source coverage and enrichment gaps — operations v1

## Goal

Prioritize the global hackathon catalog by evidence: compare which **unique
canonical editions** each approved source contributes, where sources overlap,
and which selected fields remain unknown. Registrations, prizes, eligibility,
historic editions, provenance and precision matter as much as total volume.

This is **not** a crawler, authorization grant, external truth benchmark, or
machine-learned ranking. It is a read-only analysis of the current PostgreSQL
canonical database.

## API and dashboard

Authenticated admin viewer session (or appropriately configured admin API
key):

```
GET /admin/coverage/opportunities?recent_days=30&limit=100
```

The admin **Catalog coverage** view now shows the source overlap and metadata
gaps table. It displays each source's **attributed unique**, **exclusive**,
**shared**, recent canonical creations, missing registration deadlines,
unknown participation costs and missing prize records.

### Definitions

- **Attributed:** a distinct active, unmerged `hackathons.id` linked by at least
  one active `source_event_refs` record from this source. Multiple references
  to the same edition from the same source count once.
- **Exclusive:** attributed canonical edition with active references to exactly
  one distinct source **within our own database**. This is not evidence of
  global exclusivity or future marginal lift.
- **Shared:** attributed canonical edition with references from two or more
  distinct sources. It appears in several per-source rows, but only once in
  the global distinct count.
- **Unknown field:** missing in the *current canonical event*. It does not
  prove that the source omitted information, or that the value is false/zero.
  Example: an unknown eligibility status is not worldwide eligibility.
- **Recent CREATED/LINKED/UPDATED/REJECTED:** immutable processing outcomes
  recorded during the selected UTC rolling period. These may include old
  imported editions, and CREATED may later be merged.
- **Unattributed:** active canonical hackathons without any active linked source
  references. Such rows are not silently assigned a source.

Inactive references and inactive/merged canonicals are excluded from
source-cohort metrics. Historic references from disabled or unapproved sources
can still be counted as *attribution*, but never grant collection rights.

The recommendations (`VALIDATE_WITH_CONTROLLED_COLLECTION`,
`ENRICH_CANONICAL_FIELDS`, `INVESTIGATE_SOURCE_OVERLAP` etc.) are
**operator hints only**. They do not change source status, scheduler priority,
source permissions or make network requests.

## Safety

- Admin-only GET, existing authentication and request parameter bounds:
  `recent_days` 1–90, `limit` 1–100.
- Read-only SQL with parameterized time interval and limit.
- No raw source HTML, credential, personal information or failure text.
- Multi-source overlap is correctly deduplicated by
  `SELECT DISTINCT source_id,hackathon_id`, before aggregation; the
  global distinct count is not summed over per-source results.
- The admin UI never offers a "turn on this source" action based on diagnostic
  observations. A crawl still requires the existing independent registry and
  DB-owned permission gates.

## Acceptance and verification

```bash
pytest -q apps/api/tests/test_source_opportunities.py
APP_ENV=test HIP_ALLOW_SOURCE_OPPORTUNITIES_DRILL=1 \
DATABASE_URL="<disposable local PostgreSQL>" \
python -m scripts.ops.source_opportunities_integration_check
```

CI runs the PostgreSQL check after migrations; fixtures are synthetic and
rolled back. Assertions cover two overlapping sources, repeated source
references, exclusive/shared global counts, missing-field semantics, a
source awaiting permission review and recent vs historical CREATED outcomes.

## Next roadmap steps

1. Merge the currently open dependency chains after code review:
   `#3 → #9 → #10 → #11`, and `#5 → #8`.
2. Run specifically authorized batch collection in staging to measure actual
   unique event growth and field completeness **before and after** ingestion.
3. Prioritize new legal API/partner integrations by real marginal additions,
   rather than source registry count alone.
4. Expand normalization of eligibility, prizes, deadlines, time zones and
   source field selections; add richer filtering/faceting on those fields.
5. Establish repeatable production deployment gates, freshness monitoring and
   publisher/organizer validation samples.

**No real-world production coverage gain has been claimed in this PR.**
