"""Optional, bounded source-dispatch loop for the W6 adaptive scheduler."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from hackathon_sources.factory import build_adapter, live_adapter_keys
from hackathon_sources.fetcher import FetchEngine
from hackathon_sources.persistent_runner import run_source_persisted
from hackathon_sources.registry import SourceRegistryRepository, load_source_definitions
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


async def run_scheduler_cycle(
    engine: Engine,
    *,
    root: Path,
    dispatch: bool = False,
    limit: int = 3,
    max_pages: int = 5,
) -> DispatchResult:
    """Plan health/recrawls and optionally execute approved, due live adapters.

    The DB claim is committed *before* network activity. The persisted runner
    rechecks the operator pause switch on every page. A failed source cannot
    abort other sources in this batch.
    """
    if not 1 <= limit <= 20 or not 1 <= max_pages <= 20:
        raise ValueError("limit and max_pages must be between 1 and 20")
    definitions = load_source_definitions(root / "db/seeds/sources_v0_1.json")
    definitions = [d for d in definitions if d.key in live_adapter_keys()]
    # Register only known live adapters, without network access. Operational
    # overrides on existing records are never reset by this seed import.
    with engine.begin() as conn:
        registry = SourceRegistryRepository(conn)
        for definition in definitions:
            registry.upsert(definition)
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
    claimed = succeeded = failed = 0
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
    )
