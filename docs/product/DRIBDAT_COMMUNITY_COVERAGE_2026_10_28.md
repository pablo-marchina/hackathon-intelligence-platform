# Two organizer-published Dribdat APIs: Hack4SocialGood and Echtzeit/Graffathon

Date: 2026-10-28

## Scope

This increment reuses `DribdatOpenEventsAdapter` rather than inventing a new
crawler. Both organizers publicly advertise `/api/events.json` on their own
About/Open Data pages. Endpoints are read-only, single-page and host-scoped.

| Source key | Public API | Organizer evidence |
| --- | --- | --- |
| `dribdat_hack4socialgood` | `https://bd.hack4socialgood.ch/api/events.json` | https://bd.hack4socialgood.ch/about/ ; https://bd.hack4socialgood.ch/ |
| `dribdat_echtzeit` | `https://make.echtzeitkultur.org/api/events.json` | https://make.echtzeitkultur.org/about/ ; https://make.echtzeitkultur.org/ |

The Hack4SocialGood site lists social-tech hackathons from 2020 and
2023–2026 and also a Data Toolbox; only hackathon-like editions should be
canonicalized. The Echtzeit portal includes Graffathon 2022 (described by
the organizer as a two-day hackathon), workshops and demo activities.
Do **not** mark every event or generic workshop as a hackathon.

## Classification and data quality

- A strictly bounded `Hack4SocialGood` followed optionally by a 20xx year
  is accepted **only** when it comes from the approved Hack4SocialGood source.
  Other page content such as `Data Toolbox` is not implicitly accepted.
- The publisher-documented `GRAFFATHON 2022` is accepted in the Echtzeit
  source; the 2026 `ECHTZEIT GRAFFATHON` is described as a workshop and
  is not promoted without additional event-specific evidence.
- All other events still require hackathon, buildathon, datathon, game-jam,
  ideathon, GLAMhack or hackdays names or exact publisher hashtags.
- Source IDs, dates, original description, URL and selected fields retain
  provenance. Missing registration windows, price, eligibility and prize
  details remain unknown, not guessed.
- Do not fetch third-party `webpage_url`, project, participant or
  submission pages using approval inherited from the organizer feed.
- A shared event appearing in several sources must not be counted more than
  once. The source contribution metric and global unique active delta are
  different. Idempotent canonical replay and merge decisions are independent
  of these source registry changes.

## Operational gates

1. Complete PR #23 (opt-in scheduler) and PR #24 (Hackalendar), or
   equivalently verify their underlying functionality in `main`, before
   merging this stacked PR.
2. On protected `main`, use manual GitHub Actions
   `staging-coverage-campaign` profile `dribdat_community` in
   **plan** mode. The profile has exactly two entries.
3. Provision a dedicated, migrated and secret-managed staging database.
   A source appearing as `ALLOWED` in the code is **not** proof that its
   database row is permitted. Explicit independent operator approval is
   required for both `domain + source_family` records. If either is
   absent, revoked or paused, the preflight must refuse all network work.
4. After approval and source-access review, use protected **apply** with
   `max_pages=1`, `max_versions=25`, and the exact manual confirmation.
   Review redacted evidence per source (fetched pages, staged refs,
   CREATED/LINKED/UPDATED/REJECTED, unresolved backlog).
5. Repeat under measured limits, check freshness and corrected duplicates,
   and only then tune scheduled operation. The scheduler stays disabled
   by default; no infrastructure or live database was provisioned here.
6. Never treat synthetic CI fixtures or registry counts as actual new
   hackathons. Real staging and production catalog counts remain
   unverified until an authorized campaign produces receipts.

## Safety test contract

`packages/source_sdk/tests/test_dribdat_community_sources.py`
runs entirely offline. It checks registered exact hosts, reusable adapters,
legitimate series editions, rejected workshops and other generic events,
deduplicated native IDs and no implicit crawling of organizer links.

Follow-up: expand beyond these two organizer feeds with independently
reviewed original sources in more regions, especially events in Asia,
Africa and Latin America.
