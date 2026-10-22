"""Lease renewal is fail-closed and independent of the ingest connection."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from hackathon_operations.lease_heartbeat import LeaseHeartbeat, LeaseLostError
from hackathon_operations.source_maintenance import LEASE_DURATION, renew_lease


class FakeRow:
    def __init__(self, found: bool) -> None:
        self.found = found

    def first(self) -> tuple[str] | None:
        return ("source-id",) if self.found else None


class FakeConnection:
    def __init__(self, found: bool) -> None:
        self.found = found
        self.sql = ""
        self.params: dict[str, object] = {}

    def execute(self, statement: object, params: dict[str, object]) -> FakeRow:
        self.sql = str(statement)
        self.params = params
        return FakeRow(self.found)


def test_renewal_uses_fencing_and_operator_policy() -> None:
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    conn = FakeConnection(True)
    assert renew_lease(conn, source_id="source", lease_token="token", now=now)
    assert "lease_token=CAST(:token AS uuid)" in conn.sql
    assert "st.lease_until > clock.tick" in conn.sql
    assert "s.permission_status='ALLOWED'" in conn.sql
    assert "s.detail_extraction_enabled" in conn.sql
    assert conn.params["lease_seconds"] == int(LEASE_DURATION.total_seconds())
    assert conn.params["now"] == now
    assert not renew_lease(FakeConnection(False), source_id="source", lease_token="stale", now=now)


def test_heartbeat_fail_closed_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LeaseHeartbeat, "_renew", lambda self: False)

    async def run() -> None:
        with pytest.raises(LeaseLostError, match="no longer active"):
            async with LeaseHeartbeat(None, source_id="s", lease_token="t"):  # type: ignore[arg-type]
                pytest.fail("network must not start")

    asyncio.run(run())


def test_heartbeat_detects_lease_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def renew(self: LeaseHeartbeat) -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    monkeypatch.setattr(LeaseHeartbeat, "_renew", renew)

    async def run() -> None:
        async with LeaseHeartbeat(
            None,
            source_id="s",
            lease_token="t",
            interval_seconds=0.005,  # type: ignore[arg-type]
        ) as heartbeat:

            async def await_lease_loss() -> None:
                while not heartbeat.lost:
                    await asyncio.sleep(0.002)

            await asyncio.wait_for(await_lease_loss(), timeout=0.5)
            assert heartbeat.lost

    asyncio.run(run())
    assert calls >= 2


def test_heartbeat_database_failure_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def renew(self: LeaseHeartbeat) -> bool:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ConnectionError("credential in error must not be logged")
        return True

    monkeypatch.setattr(LeaseHeartbeat, "_renew", renew)

    async def run() -> None:
        async with LeaseHeartbeat(
            None,
            source_id="s",
            lease_token="t",
            interval_seconds=0.005,  # type: ignore[arg-type]
        ) as heartbeat:

            async def await_lease_loss() -> None:
                while not heartbeat.lost:
                    await asyncio.sleep(0.002)

            await asyncio.wait_for(await_lease_loss(), timeout=0.5)
            assert heartbeat.lost

    asyncio.run(run())


def test_lease_completion_requires_unexpired_ownership() -> None:
    from hackathon_operations.source_maintenance import complete_lease

    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    active = FakeConnection(True)
    assert complete_lease(active, source_id="source", lease_token="token", now=now, success=True)
    assert "lease_until > clock.tick" in active.sql
    assert "lease_token=CAST(:token AS uuid)" in active.sql
    assert active.params["now"] == now
    assert not complete_lease(
        FakeConnection(False),
        source_id="source",
        lease_token="expired",
        now=now,
        success=True,
    )
