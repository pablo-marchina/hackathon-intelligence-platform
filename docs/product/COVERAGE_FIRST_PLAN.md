# Coverage-first product direction (2026-10-19)

## Product objective

Build a global, multilingual, continually updated **hackathon intelligence index**.
Optimize for **unique, verifiable hackathons with useful decision-making metadata**, not
raw scraping volume or a misleading counter of unreviewed URLs.

Three independent quantities should always be reported:

1. **Source pipeline:** configured domains; legally/operationally approved sources; sources with a successful recent crawl.
2. **Event discovery:** candidate URLs and raw event references; classifier-accepted event records; deduplicated active canonical hackathons.
3. **Decision quality:** fraction of events with verified registration deadlines, eligibility, team requirements, tracks, fees, prizes, location, links and evidence.

The W2 reference corpus contained **250 deduplicated active canonical hackathons** when measured. That is a historical result, **not** a live current catalog count.
The registry currently has **23 configured sources**, only **4 eligible to fetch by configuration** and **19 pending independent policy review**. Running `python scripts/source_coverage_report.py` provides the current registry snapshot. Eligibility by config is not proof of successful fetching.

## Acquisition strategy

| Tier | Source categories | What we can do | Gate |
| --- | --- | --- | --- |
| 1 | Officially documented/public authorized event APIs | Refresh incremental listings and retain provenance | Access contract, rate limits, retry and event dedup |
| 2 | Official organizer and university pages with structured data | Extract schema.org JSON-LD on explicit approved seed URLs | **ALLOWED + detail_extraction_enabled** in source registry; robots/rate/egress checks |
| 3 | RSS/Atom and sitemaps from authorized websites | Discover candidate URLs and then separate host-level policies | Dedicated source allowlist and reviewed terms |
| 4 | Broad search in 10+ languages | Identify new organizers, sources and official event pages | Discovery is not authorization to fetch details |
| 5 | Partner-only/restricted portals | Request permission and use documented partner integrations | Never bypass access limits, authentication, robots or terms |

The generic HTML adapter added in this iteration only processes HTML pages already fetched from **permitted** sources. It **does not follow** external `official_url`, registration or sponsor links. New source candidates stay disabled for fetching until authorized. The existing W3 crawler and the historical W2 backfill are separate pipelines; this iteration does not claim to make every newly parsed field searchable canonically without a normalization/reconciliation pass.

## Field completeness target

Group fields by provenance and confidence. Preserve a raw source observation even when the canonical value is unknown or contradictory.

| Category | Desired attributes |
| --- | --- |
| Identity | Name, slug, edition/year, series, official website, host platform, language, links |
| Timing | Announcement, application/registration windows, hackathon start/end, milestones, timezone, last verified |
| Participation | Remote/on-site/hybrid, venue, geography, eligible residency/countries, student status, age, experience, individual/team, team size |
| Cost and awards | Entry fee, fee currency, free tickets, total prize pool, per-track awards, cash/noncash, travel grants |
| Themes | Tracks, tags, technologies, industries, beginner friendliness, expected deliverables |
| People and program | Organizer, university, sponsors, judges, mentors, workshops, judging criteria, teams |
| Reliability | Source URL, field-level evidence, confidence, crawl time, license/policy, changes/conflicts and freshness |

Unknown is not false: avoid representing a missing age restriction as open-to-all, a missing prize as zero, or a missing deadline as indefinitely open.

## Filters

The API already offers keyword, registration state, status, event type, format, country, city, start/deadline windows, fees, USD prize range, tracks, technologies, organizer/sponsor, team size, student requirement, individual participation and eligible country.

This iteration exposes these on the `/search` page. Future filters require **both** an ingestible canonical field and an index/query contract; examples: language, age range, current open applications, remote eligibility, verified-only, registration confidence, entry fee currency, timezone, duration, internships/scholarships and travel stipends.

## Milestones and acceptance criteria

1. **Coverage instrumentation:** expose `sources approved / sources running successfully`, `candidates / accepted / canonical unique`, per-field completeness, new distinct events per source per week and false positives.
2. **Onboarding scale:** verify terms and API availability for each of the 19 backlog sources; only promote individually approved sources and add parser fixtures per site.
3. **Normalization:** convert the extracted `metadata` (dates, fees, registration links, organizers, sponsors) into canonical fields with evidence and conflict resolution. Historical W2 script has a fixed four-source allowlist and must not be mistaken for an automated full-world processor.
4. **New-source discovery:** continuously discover and review organizer websites across regions and languages; assign source ownership and limits before fetch.
5. **Advanced discovery experience:** dynamic facets/counts, multi-select filters, saved filters, completeness toggles, time-zone-aware dates and inclusion/unknown states.
6. **Real-world gates:** run PostgreSQL/Redis/Celery and Next.js build; perform end-to-end extraction→normalization→dedup→search tests on several authorized real sources; measure coverage increase without introducing duplicates.

**Not yet done:** no new live site was crawled in this iteration; source policy reviews, canonical normalization of all new JSON-LD fields, Next.js build, and live infrastructure tests remain open.
