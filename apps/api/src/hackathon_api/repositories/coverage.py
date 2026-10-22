"""Read-only coverage snapshot from the *current* database, not the historical W2 corpus.

Never add per-source canonical counts to obtain the global total: one hackathon
can have references from multiple sources. Missing registration dates are unknown,
not evidence that registration is currently open.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import Connection, text


BACKLOG_FIELDS = (
    'backlog_pending', 'backlog_retry', 'backlog_claimed', 'backlog_done',
    'backlog_exhausted', 'backlog_ready_now', 'backlog_expired_claims',
)


class CoverageRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def snapshot(self, *, as_of: datetime | None = None, recent_days: int = 7) -> dict[str, Any]:
        if not 1 <= recent_days <= 90:
            raise ValueError('recent_days must be between 1 and 90')
        instant = as_of or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError('as_of must be timezone-aware')
        # Growth is measured on *calendar days* in UTC; recency for operational
        # status continues to mean a rolling window of 24-hour periods.
        instant = instant.astimezone(UTC)
        recent_since = instant - timedelta(days=recent_days)
        first_day = instant.date() - timedelta(days=recent_days - 1)
        growth_start = datetime.combine(first_day, time.min, tzinfo=UTC)
        catalog_row = self.connection.execute(text('''
            SELECT COUNT(*) FILTER (WHERE h.is_active AND h.merged_into_id IS NULL) AS unique_active,
                   COUNT(*) FILTER (WHERE h.is_active AND h.merged_into_id IS NULL
                       AND h.start_at > :as_of) AS upcoming_by_date,
                   COUNT(*) FILTER (WHERE h.is_active AND h.merged_into_id IS NULL
                       AND h.start_at <= :as_of AND h.end_at >= :as_of) AS ongoing_by_date,
                   COUNT(*) FILTER (WHERE h.is_active AND h.merged_into_id IS NULL
                       AND h.registration_open_at IS NOT NULL AND h.registration_close_at IS NOT NULL
                       AND h.registration_open_at <= :as_of AND h.registration_close_at >= :as_of)
                       AS confirmed_registration_window,
                   COUNT(*) FILTER (WHERE h.is_active AND h.merged_into_id IS NULL
                       AND h.registration_close_at IS NULL) AS unknown_registration_close,
                   COUNT(*) FILTER (WHERE h.is_active AND h.merged_into_id IS NULL
                       AND h.official_url IS NOT NULL) AS with_official_url,
                   COUNT(*) FILTER (WHERE h.is_active AND h.merged_into_id IS NULL
                       AND h.start_at IS NOT NULL AND h.end_at IS NOT NULL) AS with_start_end,
                   COUNT(*) FILTER (WHERE h.is_active AND h.merged_into_id IS NULL
                       AND h.primary_location_id IS NOT NULL) AS with_primary_location,
                   COUNT(*) FILTER (WHERE NOT h.is_active OR h.merged_into_id IS NOT NULL)
                       AS inactive_or_retired
              FROM hackathons h
        '''), {'as_of': instant}).mappings().one()
        # Aggregate each independently: joining queue items or payloads to refs
        # would multiply rows and overstate both coverage and backlog.
        source_rows = self.connection.execute(text('''
            WITH refs AS (
                SELECT r.source_id, COUNT(*) AS source_refs,
                       COUNT(DISTINCT h.id) AS attributed_unique_active,
                       COUNT(*) FILTER (WHERE r.first_seen_at >= :recent_since
                           AND r.first_seen_at <= :as_of) AS new_source_refs_recent,
                       COUNT(DISTINCT h.id) FILTER (WHERE h.created_at >= :recent_since
                           AND h.created_at <= :as_of) AS new_attributed_canonicals_recent
                  FROM source_event_refs r
                  LEFT JOIN hackathons h ON h.id = r.hackathon_id
                      AND h.is_active AND h.merged_into_id IS NULL
                 WHERE r.active
                 GROUP BY r.source_id
            ), queue AS (
                SELECT b.source_id,
                       COUNT(*) FILTER (WHERE kind = 'DISCOVERED') AS discovered_urls,
                       COUNT(*) FILTER (WHERE kind = 'DISCOVERED' AND state = 'DONE') AS fetched_discovered_urls,
                       COUNT(*) FILTER (WHERE state = 'PENDING') AS pending,
                       COUNT(*) FILTER (WHERE state = 'RETRY') AS retry,
                       COUNT(*) FILTER (WHERE state = 'CLAIMED') AS claimed,
                       COUNT(*) FILTER (WHERE state = 'DONE') AS done,
                       COUNT(*) FILTER (WHERE state = 'EXHAUSTED') AS exhausted,
                       COUNT(*) FILTER (WHERE state IN ('PENDING','RETRY')
                           AND next_fetch_at <= :as_of) AS ready_now,
                       COUNT(*) FILTER (WHERE state = 'CLAIMED'
                           AND claim_until <= :as_of) AS expired_claims
                  FROM source_url_backlog b
                 GROUP BY b.source_id
            )
            SELECT s.id::text AS source_id, s.name, s.domain, s.permission_status,
                   s.active, s.last_success_at,
                   COALESCE(r.source_refs, 0) AS source_refs,
                   COALESCE(r.attributed_unique_active, 0) AS attributed_unique_active,
                   COALESCE(r.new_source_refs_recent, 0) AS new_source_refs_recent,
                   COALESCE(r.new_attributed_canonicals_recent, 0) AS new_attributed_canonicals_recent,
                   COALESCE(q.discovered_urls, 0) AS discovered_urls,
                   COALESCE(q.fetched_discovered_urls, 0) AS fetched_discovered_urls,
                   COALESCE(q.pending, 0) AS backlog_pending,
                   COALESCE(q.retry, 0) AS backlog_retry,
                   COALESCE(q.claimed, 0) AS backlog_claimed,
                   COALESCE(q.done, 0) AS backlog_done,
                   COALESCE(q.exhausted, 0) AS backlog_exhausted,
                   COALESCE(q.ready_now, 0) AS backlog_ready_now,
                   COALESCE(q.expired_claims, 0) AS backlog_expired_claims
              FROM sources s
              LEFT JOIN refs r ON r.source_id = s.id
              LEFT JOIN queue q ON q.source_id = s.id
             ORDER BY attributed_unique_active DESC, s.name ASC
        '''), {'as_of': instant, 'recent_since': recent_since}).mappings().all()
        pipeline_row = self.connection.execute(text('''
            SELECT COUNT(*) AS staged_payloads,
                   COUNT(*) FILTER (WHERE processed_hash IS DISTINCT FROM content_hash)
                       AS awaiting_normalization,
                   COUNT(*) FILTER (WHERE processed_hash = content_hash)
                       AS normalized_current_version,
                   COUNT(*) FILTER (WHERE processed_hash IS DISTINCT FROM content_hash
                       AND last_error IS NOT NULL) AS normalization_errors
              FROM source_event_payloads
        ''')).mappings().one()
        completeness_row = self.connection.execute(text('''
            SELECT COUNT(*) FILTER (WHERE h.format <> 'UNKNOWN') AS known_format,
                   COUNT(*) FILTER (WHERE h.registration_url IS NOT NULL) AS with_registration_url,
                   COUNT(*) FILTER (WHERE h.registration_close_at IS NOT NULL)
                       AS with_registration_deadline,
                   COUNT(*) FILTER (WHERE h.is_free IS NOT NULL
                       OR h.participation_fee IS NOT NULL) AS known_participation_cost,
                   COUNT(*) FILTER (WHERE h.eligible_worldwide IS NOT NULL)
                       AS known_worldwide_eligibility,
                   COUNT(*) FILTER (WHERE h.short_description IS NOT NULL
                       OR h.full_description IS NOT NULL) AS with_description,
                   COUNT(*) FILTER (WHERE h.last_verified_at >= :recent_since)
                       AS verified_recently,
                   COUNT(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM hackathon_organizations ho
                       WHERE ho.hackathon_id=h.id AND ho.role IN ('ORGANIZER', 'CO_ORGANIZER', 'HOST')
                   )) AS with_organizer,
                   COUNT(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM hackathon_organizations ho
                       WHERE ho.hackathon_id=h.id AND ho.role='SPONSOR'
                   )) AS with_sponsor,
                   COUNT(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM hackathon_tracks ht WHERE ht.hackathon_id=h.id
                   )) AS with_tracks,
                   COUNT(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM hackathon_technologies ht WHERE ht.hackathon_id=h.id
                   )) AS with_technologies,
                   COUNT(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM prize_pools pp WHERE pp.hackathon_id=h.id
                   ) OR EXISTS (
                       SELECT 1 FROM prizes p WHERE p.hackathon_id=h.id
                   )) AS with_prizes,
                   COUNT(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM eligibility_rules er WHERE er.hackathon_id=h.id
                   )) AS with_eligibility_rules
              FROM hackathons h
             WHERE h.is_active AND h.merged_into_id IS NULL
        '''), {'recent_since': recent_since}).mappings().one()
        # Grouping sets give both per-source and GLOBAL DISTINCT totals in one
        # source-reference query. These stages share a reference cohort; legacy
        # references without durable payloads are kept in the denominator.
        # Rejected records have processed_hash=content_hash too, so success
        # explicitly requires last_error IS NULL.
        funnel_rows = self.connection.execute(text('''
            SELECT GROUPING(r.source_id) AS is_global,
                   CASE WHEN GROUPING(r.source_id) = 1 THEN NULL
                        ELSE r.source_id::text END AS source_id,
                   COUNT(*) AS active_references,
                   COUNT(*) FILTER (WHERE p.source_event_ref_id IS NOT NULL)
                       AS staged_references,
                   COUNT(*) FILTER (WHERE p.source_event_ref_id IS NOT NULL
                       AND p.processed_hash IS DISTINCT FROM p.content_hash)
                       AS pending_references,
                   COUNT(*) FILTER (WHERE p.processed_hash = p.content_hash
                       AND p.last_error IS NULL) AS accepted_references,
                   COUNT(*) FILTER (WHERE p.processed_hash = p.content_hash
                       AND p.last_error IS NOT NULL) AS rejected_references,
                   COUNT(*) FILTER (WHERE p.last_error IS NOT NULL
                       AND p.processed_hash IS DISTINCT FROM p.content_hash)
                       AS failed_pending_references,
                   COUNT(*) FILTER (WHERE p.processed_hash = p.content_hash
                       AND p.last_error IS NULL AND h.id IS NOT NULL)
                       AS linked_searchable_references,
                   COUNT(DISTINCT h.id) FILTER (WHERE p.processed_hash = p.content_hash
                       AND p.last_error IS NULL) AS unique_searchable_hackathons
              FROM source_event_refs r
              LEFT JOIN source_event_payloads p
                ON p.source_event_ref_id = r.id AND p.source_id = r.source_id
              LEFT JOIN hackathons h ON h.id = r.hackathon_id
                AND h.is_active AND h.merged_into_id IS NULL
             WHERE r.active AND r.source_id IS NOT NULL
             GROUP BY GROUPING SETS ((r.source_id), ())
             ORDER BY is_global DESC, source_id
        ''')).mappings().all()
        # Count only currently active, non-merged canons. A new canonical row is
        # *not* necessarily a newly scheduled hackathon: historical backfills also
        # appear here. Never add source attribution to the global total.
        daily_growth_rows = self.connection.execute(text('''
            SELECT (h.created_at AT TIME ZONE 'UTC')::date AS day,
                   COUNT(*) AS new_active_canonicals
              FROM hackathons h
             WHERE h.is_active AND h.merged_into_id IS NULL
               AND h.created_at >= :growth_start AND h.created_at <= :as_of
             GROUP BY (h.created_at AT TIME ZONE 'UTC')::date
             ORDER BY day
        '''), {'growth_start': growth_start, 'as_of': instant}).mappings().all()
        observed_days = {str(row['day']): int(row['new_active_canonicals']) for row in daily_growth_rows}
        growth_days = [
            {'date': (first_day + timedelta(days=day)).isoformat(),
             'new_active_canonicals': observed_days.get((first_day + timedelta(days=day)).isoformat(), 0)}
            for day in range(recent_days)
        ]
        # These are separate sets of measurements. "Active" is not an authorization:
        # both the source's permission and its operational state matter.
        funnel_fields = (
            'active_references', 'staged_references', 'pending_references',
            'accepted_references', 'rejected_references', 'failed_pending_references',
            'linked_searchable_references', 'unique_searchable_hackathons',
        )
        def funnel_metrics(data: dict[str, Any]) -> dict[str, Any]:
            counts = {key: int(data.get(key) or 0) for key in funnel_fields}
            def rate(numerator: str, denominator: str) -> float | None:
                total = counts[denominator]
                return round(counts[numerator] * 100 / total, 1) if total else None
            return {
                **counts,
                'legacy_unstaged_references': counts['active_references'] - counts['staged_references'],
                'accepted_unlinked_references': counts['accepted_references'] - counts['linked_searchable_references'],
                'staging_percent': rate('staged_references', 'active_references'),
                'acceptance_percent': rate('accepted_references', 'staged_references'),
                'linkage_percent': rate('linked_searchable_references', 'accepted_references'),
                'distinct_hackathons_percent': rate('unique_searchable_hackathons', 'linked_searchable_references'),
            }
        funnel_by_source = {
            str(entry['source_id']): funnel_metrics(dict(entry))
            for entry in funnel_rows if not entry['is_global'] and entry['source_id'] is not None
        }
        global_funnel = next((funnel_metrics(dict(entry)) for entry in funnel_rows if entry['is_global']),
                             funnel_metrics({}))
        rows = []
        for entry in source_rows:
            row = dict(entry)
            last = row.get('last_success_at')
            row['recent_success'] = bool(last and last >= recent_since)
            row['attributed_unique_active'] = int(row['attributed_unique_active'])
            row['source_refs'] = int(row['source_refs'])
            row['new_source_refs_recent'] = int(row.get('new_source_refs_recent') or 0)
            row['new_attributed_canonicals_recent'] = int(row.get('new_attributed_canonicals_recent') or 0)
            for field in BACKLOG_FIELDS + ('discovered_urls', 'fetched_discovered_urls'):
                row[field] = int(row.get(field) or 0)
            row['funnel'] = funnel_by_source.get(str(row['source_id']), funnel_metrics({}))
            row['discovery_fetch_percent'] = (
                round(row['fetched_discovered_urls'] * 100 / row['discovered_urls'], 1)
                if row['discovered_urls'] else None
            )
            rows.append(row)
        catalog = {key: int(value) for key, value in dict(catalog_row).items()}
        completeness = {key: int(value) for key, value in dict(completeness_row).items()}
        denominator = catalog['unique_active']
        # Empty catalog is N/A, not "0% of hackathons are complete".
        completeness_rates = {
            key: round(count * 100 / denominator, 1) if denominator else None
            for key, count in completeness.items()
        }
        pipeline = {key: int(value) for key, value in dict(pipeline_row).items()}
        backlog_totals = {key: sum(row[key] for row in rows) for key in BACKLOG_FIELDS}
        return {
            'as_of': instant.isoformat(),
            'recent_window_days': recent_days,
            'source_counts': {
                'registered_in_database': len(rows),
                'active_in_database': sum(bool(row['active']) for row in rows),
                'approved_and_active': sum(
                    bool(row['active']) and row['permission_status'] == 'ALLOWED' for row in rows
                ),
                'recent_success': sum(row['recent_success'] for row in rows),
            },
            'catalog': catalog,
            'growth': {
                'calendar_timezone': 'UTC',
                'calendar_days': recent_days,
                'new_active_canonicals': sum(row['new_active_canonicals'] for row in growth_days),
                'daily': growth_days,
            },
            'field_completeness': {'counts': completeness, 'percentages': completeness_rates},
            'pipeline': {**pipeline, 'backlog': backlog_totals},
            'conversion_funnel': {
                'discovered_url_tasks': sum(row['discovered_urls'] for row in rows),
                'fetched_discovered_url_tasks': sum(row['fetched_discovered_urls'] for row in rows),
                'fetch_percent': (
                    round(100 * sum(row['fetched_discovered_urls'] for row in rows)
                          / sum(row['discovered_urls'] for row in rows), 1)
                    if sum(row['discovered_urls'] for row in rows) else None
                ),
                **global_funnel,
            },
            'sources': rows,
            'methodology': {
                'unique_active': 'Active, non-merged hackathons; NOT the sum of source counts.',
                'confirmed_registration_window': 'Both registration_open_at and registration_close_at are required; unknown is not open.',
                'recent_success': 'sources.last_success_at within the requested time window; not a guarantee of fresh events.',
                'attributed_unique_active': 'Distinct active canonical IDs referenced by each source; counts can overlap.',
                'field_completeness': 'Per unique active canonical; NULL percentage means no events. Unknown values are not inferred.',
                'pipeline': 'Staged payloads count source records, not unique hackathons. Backlog counts URL tasks by source.',
                'backlog_ready_now': 'Due PENDING/RETRY only; expired CLAIMED leases reported separately.',
                'new_active_canonicals': 'Active, unmerged canonical rows created in the UTC calendar window. includes imports and backfills; not necessarily future or newly scheduled events. Historical counts may decrease after merge/deactivation.',
                'new_source_refs_recent': 'Currently active source references first recorded in the rolling interval; overlaps between sources and may point to older events.',
                'new_attributed_canonicals_recent': 'Active canonicals created in the rolling interval and attributed to a source; overlaps across sources.',
                'conversion_funnel': 'URL tasks are source-scoped; event references are separate units, not a direct URL-to-event conversion rate. The reference stages track active refs with an authorized durable payload. Rejected records are not successful normalizations. Unique searchable hackathons are DISTINCT across sources among successfully normalized linked references, not the total catalog.',
                'conversion_rates': 'Only nested same-unit cohorts have percentage rates. NULL means no denominator; historical refs without payloads count as unstaged. URL task fetch percentage is not a hackathon discovery success rate.',
                'growth': 'Zero days are explicit. The final day is partial through as_of; no forecasting or inference.',
                'scope': 'Current connected database only; historical W2 event totals are not substituted.',
            },
        }
