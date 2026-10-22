"""Read-only operational diagnostics for source discovery.

Historical crawl_runs are used as recorded, without pretending that their
`unique_event_count` is canonical deduplication or that the current payload
rejection classification is a historical time series.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import log1p
from typing import Any

from sqlalchemy import Connection, text


class SourceInsightsRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def snapshot(
        self, *, as_of: datetime | None = None, recent_days: int = 7,
        run_limit: int = 40,
    ) -> dict[str, Any]:
        if not 1 <= recent_days <= 90:
            raise ValueError('recent_days must be between 1 and 90')
        if not 1 <= run_limit <= 200:
            raise ValueError('run_limit must be between 1 and 200')
        instant = as_of or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError('as_of must be timezone-aware')
        instant = instant.astimezone(UTC)
        since = instant - timedelta(days=recent_days)
        params = {'since': since, 'as_of': instant}

        # Keep ref and URL cohorts independent: one source may report multiple
        # events per URL and an event may appear in several sources.
        sources = self.connection.execute(text('''
            WITH refs AS (
                SELECT source_id,
                       COUNT(*) FILTER (WHERE first_seen_at >= :since
                           AND first_seen_at <= :as_of) AS new_refs
                  FROM source_event_refs WHERE active GROUP BY source_id
            ), due AS (
                SELECT source_id,
                       COUNT(*) FILTER (WHERE state IN ('PENDING', 'RETRY')
                           AND next_fetch_at <= :as_of) AS ready_urls,
                       COUNT(*) FILTER (WHERE state = 'EXHAUSTED') AS exhausted_urls
                  FROM source_url_backlog GROUP BY source_id
            )
            SELECT s.id::text AS source_id, s.name, s.active,
                   s.permission_status, s.discovery_enabled, s.detail_extraction_enabled,
                   s.ingestion_mode::text AS ingestion_mode, s.priority,
                   COALESCE(r.new_refs, 0) AS new_refs,
                   COALESCE(d.ready_urls, 0) AS ready_urls,
                   COALESCE(d.exhausted_urls, 0) AS exhausted_urls
              FROM sources s
              LEFT JOIN refs r ON r.source_id = s.id
              LEFT JOIN due d ON d.source_id = s.id
             ORDER BY s.name, s.id
        '''), params).mappings().all()

        # Lifetime crawl runs remain in crawl_runs; these aggregates use ALL
        # runs in the selected interval (not only the limited history list).
        aggregates = self.connection.execute(text('''
            SELECT source_id::text AS source_id,
                   COUNT(*) AS run_count,
                   COUNT(*) FILTER (WHERE status = 'SUCCEEDED') AS succeeded,
                   COUNT(*) FILTER (WHERE status = 'PARTIAL') AS partial,
                   COUNT(*) FILTER (WHERE status = 'FAILED') AS failed,
                   COUNT(*) FILTER (WHERE status = 'CANCELLED') AS cancelled,
                   COUNT(*) FILTER (WHERE status = 'RUNNING') AS running,
                   COALESCE(SUM(fetched_count), 0) AS fetched_pages,
                   COALESCE(SUM(parsed_count), 0) AS parsed_records,
                   COALESCE(SUM(failed_count), 0) AS reported_failures
              FROM crawl_runs
             WHERE started_at >= :since AND started_at <= :as_of
             GROUP BY source_id
        '''), params).mappings().all()
        runs_by_source = {str(row['source_id']): dict(row) for row in aggregates}

        # This is a *current state* breakdown of staged records, NOT counts of
        # historical rejections. Do not emit raw last_error text (can contain
        # exception details or sensitive source payload data).
        reasons = self.connection.execute(text('''
            WITH classes AS (
                SELECT source_id,
                       CASE
                         WHEN last_error = 'not_hackathon_like_or_missing_name'
                              AND processed_hash = content_hash
                           THEN 'NOT_HACKATHON_OR_MISSING_NAME'
                         WHEN processed_hash IS DISTINCT FROM content_hash
                           THEN 'PROCESSING_ERROR_PENDING'
                         ELSE 'OTHER_PROCESSED_REJECTION'
                       END AS reason
                  FROM source_event_payloads
                 WHERE last_error IS NOT NULL
            )
            SELECT source_id::text AS source_id, reason, COUNT(*) AS count
              FROM classes
             GROUP BY source_id, reason
             ORDER BY source_id, reason
        ''')).mappings().all()
        by_source_reasons: dict[str, dict[str, int]] = {}
        for row in reasons:
            by_source_reasons.setdefault(str(row['source_id']), {})[str(row['reason'])] = int(row['count'])

        latest_runs = self.connection.execute(text('''
            SELECT id::text AS id, source_id::text AS source_id,
                   status::text AS status, started_at, finished_at,
                   discovered_count, fetched_count, parsed_count,
                   failed_count, valid_event_count, unique_event_count
              FROM crawl_runs
             WHERE started_at >= :since AND started_at <= :as_of
             ORDER BY started_at DESC, id DESC
             LIMIT :limit
        '''), {**params, 'limit': run_limit}).mappings().all()

        results: list[dict[str, Any]] = []
        for item in sources:
            data = dict(item)
            sid = str(data['source_id'])
            run_info = runs_by_source.get(sid, {})
            runs = {key: int(run_info.get(key) or 0) for key in (
                'run_count', 'succeeded', 'partial', 'failed', 'cancelled', 'running',
                'fetched_pages', 'parsed_records', 'reported_failures',
            )}
            errors = by_source_reasons.get(sid, {})
            new_refs = int(data['new_refs'])
            ready = int(data['ready_urls'])
            exhausted = int(data['exhausted_urls'])
            eligible = bool(
                data['active'] and data['permission_status'] == 'ALLOWED'
                and data['discovery_enabled'] and data['detail_extraction_enabled']
                and data['ingestion_mode'] not in ('BLOCKED', 'DISCOVERY_ONLY', 'MANUAL')
            )
            # Diagnostic heuristic only; do NOT mutate scheduler priority or
            # activate any source. A source without runs cannot be scored reliably.
            if not eligible:
                score = None
                action = 'REVIEW_PERMISSIONS_OR_PAUSE'
            elif not runs['run_count']:
                score = None
                action = 'VALIDATE_WITH_CONTROLLED_RUN'
            else:
                success_fraction = runs['succeeded'] / runs['run_count']
                penalty = min(20, 5 * runs['failed'] + 2 * runs['partial'] + exhausted)
                score = round(max(0, min(100,
                    15 * log1p(new_refs) / log1p(50)
                    + min(30, ready * 2)
                    + 25 * success_fraction
                    + 10 * int(data['priority']) / 100
                    + min(20, errors.get('PROCESSING_ERROR_PENDING', 0) * 2)
                    - penalty,
                )), 1)
                if errors.get('PROCESSING_ERROR_PENDING', 0) or runs['failed'] > runs['succeeded']:
                    action = 'INVESTIGATE_FAILURES'
                elif ready:
                    action = 'PROCESS_AUTHORIZED_BACKLOG'
                else:
                    action = 'MONITOR_AND_EXPAND_EVIDENCE'
            results.append({
                'source_id': sid, 'name': str(data['name']),
                'eligible_for_controlled_collection': eligible,
                'permission_status': data['permission_status'],
                'active': bool(data['active']),
                'new_references_in_window': new_refs,
                'ready_urls_now': ready,
                'exhausted_urls': exhausted,
                'runs_in_window': runs,
                'current_rejection_reasons': errors,
                'triage_score': score,
                'suggested_action': action,
            })
        results.sort(key=lambda row: (row['triage_score'] is None,
                                      -(row['triage_score'] or 0), row['name'], row['source_id']))
        return {
            'as_of': instant.isoformat(), 'recent_window_days': recent_days,
            'sources': results,
            'recent_runs': [
                {**dict(row), 'source_id': str(row['source_id']), 'id': str(row['id']),
                 'started_at': row['started_at'].isoformat(),
                 'finished_at': row['finished_at'].isoformat() if row['finished_at'] else None}
                for row in latest_runs
            ],
            'methodology': {
                'historical_runs': 'Runs in the rolling window, including failures. A run is not a unique hackathon.',
                'current_rejection_reasons': 'Current staged payload status grouped into fixed categories; not historical totals. Raw errors are never exposed.',
                'new_references_in_window': 'Source event references first seen in the rolling window; NOT unique new hackathons and overlapping sources remain separate.',
                'triage_score': 'Read-only operational heuristic for eligible sources with run evidence. Does not authorize collection or change scheduler priority. Missing score means insufficient evidence or permission.',
                'recent_runs': 'Most recent runs across all sources (globally limited); per-source totals include all runs in the window.',
            },
        }
