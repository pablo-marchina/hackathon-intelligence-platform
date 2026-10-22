"""Read-only coverage snapshot from the *current* database, not the historical W2 corpus.

Never add per-source canonical counts to obtain the global total: one hackathon
can have references from multiple sources. Missing registration dates are unknown,
not evidence that registration is currently open.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, text


class CoverageRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def snapshot(self, *, as_of: datetime | None = None, recent_days: int = 7) -> dict[str, Any]:
        if not 1 <= recent_days <= 90:
            raise ValueError('recent_days must be between 1 and 90')
        instant = as_of or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError('as_of must be timezone-aware')
        recent_since = instant - timedelta(days=recent_days)
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
        source_rows = self.connection.execute(text('''
            SELECT s.id::text AS source_id, s.name, s.domain, s.permission_status,
                   s.active, s.last_success_at,
                   COUNT(DISTINCT r.id) AS source_refs,
                   COUNT(DISTINCT h.id) AS attributed_unique_active
              FROM sources s
              LEFT JOIN source_event_refs r ON r.source_id = s.id AND r.active = true
              LEFT JOIN hackathons h ON h.id = r.hackathon_id
                  AND h.is_active = true AND h.merged_into_id IS NULL
             GROUP BY s.id, s.name, s.domain, s.permission_status, s.active, s.last_success_at
             ORDER BY attributed_unique_active DESC, s.name ASC
        ''')).mappings().all()
        # These are separate sets of measurements. "Active" is not an authorization:
        # both the source's permission and its operational state matter.
        rows = []
        for entry in source_rows:
            row = dict(entry)
            last = row.get('last_success_at')
            row['recent_success'] = bool(last and last >= recent_since)
            row['attributed_unique_active'] = int(row['attributed_unique_active'])
            row['source_refs'] = int(row['source_refs'])
            rows.append(row)
        catalog = {key: int(value) for key, value in dict(catalog_row).items()}
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
            'sources': rows,
            'methodology': {
                'unique_active': 'Active, non-merged hackathons; NOT the sum of source counts.',
                'confirmed_registration_window': 'Both registration_open_at and registration_close_at are required; unknown is not open.',
                'recent_success': 'sources.last_success_at within the requested time window; not a guarantee of fresh events.',
                'attributed_unique_active': 'Distinct active canonical IDs referenced by each source; counts can overlap.',
                'scope': 'Current connected database only; historical W2 event totals are not substituted.',
            },
        }
