"""Centralized fail-closed operational pause checks for non-persistent workers.

PostgreSQL `sources.active` and permission policy are authoritative. Adapter
seeds MUST NOT restore disabled or restricted sources. A new transaction on each
lookup avoids stale snapshot reuse across pages when operators pause a crawl.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, text

from .models import SourceDefinition

ActivationCheck = Callable[[SourceDefinition], bool]


def check_postgres_active(engine: Engine, source: SourceDefinition) -> bool:
    """Return False for paused, deleted or unknown sources, and raise on DB failures.

    A failure to query is not treated as permission to fetch. Callers must abort,
    not catch an exception and continue without the source state check.
    """
    with engine.connect() as connection:
        active = connection.execute(
            text(
                """
                SELECT (active AND discovery_enabled AND detail_extraction_enabled
                        AND permission_status = 'ALLOWED'
                        AND ingestion_mode NOT IN ('BLOCKED','DISCOVERY_ONLY','MANUAL'))
                  FROM sources
                WHERE domain = :domain AND source_family = :family
                LIMIT 1
                """
            ),
            {"domain": source.domain, "family": source.source_family},
        ).scalar_one_or_none()
    return active is True
