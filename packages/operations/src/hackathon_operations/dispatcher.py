"""Optional, bounded source-dispatch loop for the W6 adaptive scheduler."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hackathon_intelligence.incremental import CanonicalProcessor
from hackathon_sources.factory import build_adapter, live_adapter_keys
from hackathon_sources.fetcher import FetchEngine
from hackathon_sources.models import IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.persistent_runner import run_source_persisted
from hackathon_sources.registry import load_source_definitions
from sqlalchemy import Engine, text

from .lease_heartbeat import LeaseHeartbeat, LeaseLostError
from .source_maintenance import claim_due_source, complete_lease, maintain_sources

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchResult:
    checked: int
    new_runs_processed: int
    reviews_created: int
    claimed: int
    succeeded: int
    failed: int
    canonical_processed: int = 0


_CANONICAL_OUTCOMES = ("CREATED", "LINKED", "UPDATED", "REJECTED")
_GENERIC_SCHEDULED_MODES = frozenset(
    {IngestionMode.STATIC_HTML, IngestionMode.RSS, IngestionMode.SITEMAP}
)


def select_scheduled_sources(definitions: list[SourceDefinition]) -> list[SourceDefinition]:
    """Only explicitly approved, parseable sources may enter the scheduler.

    A generic API seed has no event parser and is not scheduled. Generic
    HTML/RSS/sitemap adapters remain subject to DB authorization, robots,
    host allowlists and the persistent runner's commit fence.
    """
    known_apis = live_adapter_keys()
    return [
        definition
        for definition in definitions
        if definition.permission_status is PermissionStatus.ALLOWED
        and definition.discovery_enabled
        and definition.detail_extraction_enabled
        and definition.seed_urls
        and (
            (definition.ingestion_mode is IngestionMode.API and definition.key in known_apis)
            or definition.ingestion_mode in _GENERIC_SCHEDULED_MODES
        )
    ]


def normalize_scheduled_source(
    engine: Engine,
    *,
    source_key: str,
    max_versions: int,
    should_continue: Callable[[], bool],
) -> dict[str, int]:
    """Consume a bounded source backlog with one commit per canonical version.

    Run in a worker thread so the async lease heartbeat remains responsive.
    CanonicalProcessor rechecks DB-owned source permissions under row locks.
    The lease check before commit prevents accepting work after a lost claim.
    """
    if not 1 <= max_versions <= 1000:
        raise ValueError("canonical version budget must be 1 to 1000")
    outcomes = dict.fromkeys(_CANONICAL_OUTCOMES, 0)
    for _ in range(max_versions):
        if not should_continue():
            raise LeaseLostError("source lease lost before canonical processing")
        with engine.begin() as connection:
            processor = CanonicalProcessor(connection, source_key=source_key)
            status = processor.process_one()
            exact = processor.last_outcome or status
            if status is None:
                return outcomes
            if status == "SKIPPED_UNAUTHORIZED":
                raise PermissionError("canonical source authorization was revoked")
            if exact is None or exact not in outcomes:
                raise ValueError("unexpected canonical processing outcome")
            if not should_continue():
                raise LeaseLostError("source lease lost during canonical processing")
        outcomes[exact] += 1
    return outcomes


async def run_scheduler_cycle(
    engine: Engine,
    *,
    root: Path,
    dispatch: bool = False,
    limit: int = 3,
    max_pages: int = 5,
    max_versions: int = 100,
) -> DispatchResult:
    """Plan health/recrawls and optionally ingest/normalize approved due sources.

    The DB claim is committed *before* network activity. The persisted runner
    rechecks the operator pause switch on every page. A failed source cannot
    abort other sources in this batch.
    """
    if not 1 <= limit <= 20 or not 1 <= max_pages <= 20 or not 1 <= max_versions <= 1000:
        raise ValueError("limit/pages must be 1 to 20 and versions must be 1 to 1000")
    definitions = load_source_definitions(root / "db/seeds/sources_v0_1.json")
    definitions = select_scheduled_sources(definitions)
    # Only a separately registered/approved PostgreSQL source can be claimed.
    # Do NOT upsert seed policy here: inserting ALLOWED seeds would silently
    # grant DB permission without an independent operator approval.
    with engine.begin() as conn:
        result = maintain_sources(conn)
    if not dispatch:
        return DispatchResult(
            result.sources_checked,
            result.runs_processed,
            result.reviews_created,
            0,
            0,
            0,
        )

    keyed = {(d.domain.lower(), d.source_family): d for d in definitions}
    claimed = succeeded = failed = canonical_processed = 0
    async with FetchEngine() as fetcher:
        for _ in range(limit):
            with engine.begin() as conn:
                lease = claim_due_source(conn, allowed_sources=list(keyed))
                if lease is None:
                    break
                definition_key = (
                    conn.execute(
                        text("""
                        SELECT lower(domain::text) AS domain, source_family FROM sources
                         WHERE id=CAST(:id AS uuid)
                    """),
                        {"id": lease["source_id"]},
                    )
                    .mappings()
                    .one()
                )
            claimed += 1
            definition = keyed.get((definition_key["domain"], definition_key["source_family"]))
            ok = False
            try:
                if definition is None:
                    raise LookupError(
                        "leased source no longer exists in the local adapter registry"
                    )
                async with LeaseHeartbeat(
                    engine,
                    source_id=lease["source_id"],
                    lease_token=lease["lease_token"],
                ) as heartbeat:
                    with engine.connect() as conn:
                        run = await run_source_persisted(
                            build_adapter(definition),
                            fetcher,
                            conn,
                            max_pages=max_pages,
                            lease_token=lease["lease_token"],
                            should_continue=lambda: not heartbeat.lost,
                        )
                    if heartbeat.lost:
                        raise LeaseLostError("scheduler lease lost during source run")
                    if run.status in {"SUCCEEDED", "PARTIAL"}:
                        counts = await asyncio.to_thread(
                            normalize_scheduled_source,
                            engine,
                            source_key=definition.key,
                            max_versions=max_versions,
                            should_continue=lambda: not heartbeat.lost,
                        )
                        canonical_processed += sum(counts.values())
                    # A PARTIAL run is deliberately retried, even if some versions
                    # were committed. The canonical processor is idempotent.
                ok = run.status == "SUCCEEDED"
            except Exception as exc:
                # Isolate a broken source, but preserve a sanitized diagnostic in logs.
                # Avoid error messages here: upstream URL/headers may contain secrets.
                logger.warning("source scheduler failed (%s)", type(exc).__name__)
                ok = False
            finally:
                with engine.begin() as conn:
                    # A stale claim must not erase a lease already reissued to another worker.
                    released = complete_lease(
                        conn,
                        source_id=lease["source_id"],
                        lease_token=lease["lease_token"],
                        success=ok,
                    )
                if not released:
                    # A completion that loses the fencing check is NOT success,
                    # even if the runner finished just before the lease expired.
                    logger.warning("source scheduler lease completion was rejected")
                    ok = False
            succeeded += int(ok)
            failed += int(not ok)
    # Process newly completed runs/anomalies; leave a failed crawl isolated.
    with engine.begin() as conn:
        after = maintain_sources(conn)
    return DispatchResult(
        result.sources_checked,
        result.runs_processed + after.runs_processed,
        result.reviews_created + after.reviews_created,
        claimed,
        succeeded,
        failed,
        canonical_processed,
    )
