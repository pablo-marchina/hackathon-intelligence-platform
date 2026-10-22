"""Immutable, crawl-attributed canonical outcomes (not inferred from crawl stats)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, text


class CanonicalYieldRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def snapshot(self, *, days: int = 7, as_of: datetime | None = None) -> dict[str, Any]:
        if not 1 <= days <= 90:
            raise ValueError("days must be between 1 and 90")
        now = as_of or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        now = now.astimezone(UTC)
        since = now - timedelta(days=days)
        params = {"since": since, "as_of": now}
        rows = (
            self.connection.execute(
                text("""
            SELECT s.id::text AS source_id, s.name AS source_name,
                   COUNT(o.id) AS processed_versions,
                   COUNT(o.id) FILTER (WHERE o.outcome='CREATED') AS created,
                   COUNT(o.id) FILTER (WHERE o.outcome='LINKED') AS linked,
                   COUNT(o.id) FILTER (WHERE o.outcome='UPDATED') AS updated,
                   COUNT(o.id) FILTER (WHERE o.outcome='REJECTED') AS rejected,
                   COUNT(o.id) FILTER (WHERE o.crawl_run_id IS NULL) AS unattributed
              FROM sources s
              LEFT JOIN canonical_processing_outcomes o
                ON o.source_id=s.id AND o.processed_at >= :since AND o.processed_at <= :as_of
             GROUP BY s.id, s.name
             ORDER BY s.name, s.id
        """),
                params,
            )
            .mappings()
            .all()
        )
        sources = [
            {
                "source_id": str(r["source_id"]),
                "name": str(r["source_name"]),
                **{
                    key: int(r[key])
                    for key in (
                        "processed_versions",
                        "created",
                        "linked",
                        "updated",
                        "rejected",
                        "unattributed",
                    )
                },
            }
            for r in rows
        ]
        # Run cohorts use the original crawl ID, not event first_seen_at or
        # the time of a delayed normalization task. An old payload may have
        # no crawl ID and is explicitly labelled unattributed.
        runs = (
            self.connection.execute(
                text("""
            SELECT o.crawl_run_id::text AS crawl_run_id,
                   o.source_id::text AS source_id,
                   COUNT(*) AS processed_versions,
                   COUNT(*) FILTER (WHERE o.outcome='CREATED') AS created,
                   COUNT(*) FILTER (WHERE o.outcome='LINKED') AS linked,
                   COUNT(*) FILTER (WHERE o.outcome='UPDATED') AS updated,
                   COUNT(*) FILTER (WHERE o.outcome='REJECTED') AS rejected
              FROM canonical_processing_outcomes o
             WHERE o.processed_at >= :since AND o.processed_at <= :as_of
               AND o.crawl_run_id IS NOT NULL
             GROUP BY o.crawl_run_id, o.source_id
             ORDER BY MAX(o.processed_at) DESC, o.crawl_run_id
             LIMIT 100
        """),
                params,
            )
            .mappings()
            .all()
        )
        return {
            "as_of": now.isoformat(),
            "recent_window_days": days,
            "totals": {
                key: sum(s[key] for s in sources)
                for key in (
                    "processed_versions",
                    "created",
                    "linked",
                    "updated",
                    "rejected",
                    "unattributed",
                )
            },
            "sources": sources,
            "runs": [
                {
                    "crawl_run_id": str(r["crawl_run_id"]),
                    "source_id": str(r["source_id"]),
                    **{
                        key: int(r[key])
                        for key in (
                            "processed_versions",
                            "created",
                            "linked",
                            "updated",
                            "rejected",
                        )
                    },
                }
                for r in runs
            ],
            "methodology": {
                "created": "Durable canonical creation outcomes, not announcements or currently active events.",
                "linked": "Source record linked to a pre-existing canonical; not a new hackathon.",
                "updated": "New content version for a record already linked to a canonical.",
                "rejected": "Record version determined not to be a hackathon.",
                "unattributed": "Historical/manual staged payloads without an original crawl ID; never guessed.",
                "window": "Outcome processing time, not event dates or crawl start time. Run grouping uses original crawl IDs.",
                "runs": "Most recent 100 attributed run groups; totals include all outcomes in the window.",
            },
        }
