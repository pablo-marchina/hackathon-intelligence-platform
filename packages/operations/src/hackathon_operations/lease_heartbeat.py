"""Long-running source lease renewal independent of the ingest connection.

A source may take longer than the initial lease to crawl. Extend it using a
short, separate DB transaction and stop accepting pages when renewal fails.
"""

from __future__ import annotations

import asyncio
import logging
from types import TracebackType

from sqlalchemy import Engine

from .source_maintenance import renew_lease

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 60.0


class LeaseLostError(RuntimeError):
    """The current source owner can no longer safely fetch or persist pages."""


class LeaseHeartbeat:
    def __init__(
        self,
        engine: Engine,
        *,
        source_id: str,
        lease_token: str,
        interval_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self.engine = engine
        self.source_id = source_id
        self.lease_token = lease_token
        self.interval_seconds = interval_seconds
        self.lost = False
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def _renew(self) -> bool:
        with self.engine.begin() as conn:
            return renew_lease(conn, source_id=self.source_id, lease_token=self.lease_token)

    async def __aenter__(self) -> LeaseHeartbeat:
        # Do not initiate network work if policy or lease has changed since claim.
        try:
            owned = await asyncio.to_thread(self._renew)
        except Exception as exc:
            self.lost = True
            raise LeaseLostError("cannot verify scheduler lease") from exc
        if not owned:
            self.lost = True
            raise LeaseLostError("scheduler lease is no longer active")
        self._task = asyncio.create_task(self._loop())
        return self

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
                break
            except TimeoutError:
                pass
            try:
                if not await asyncio.to_thread(self._renew):
                    self.lost = True
                    break
            except Exception as exc:
                # DB outage is NOT permission to continue fetching.
                logger.warning("source heartbeat failed (%s)", type(exc).__name__)
                self.lost = True
                break

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
