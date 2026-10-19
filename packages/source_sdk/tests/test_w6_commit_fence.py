"""W6 commit fences: hermetic tests complement the PostgreSQL race drill."""
from __future__ import annotations

from typing import Any

import pytest

from hackathon_sources.registry import SourceRegistryRepository


class ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class RecordingConnection:
    def __init__(self, results: list[object]) -> None:
        self.results = iter(results)
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: object, params: dict[str, Any]) -> ScalarResult:
        self.queries.append((str(statement), params))
        return ScalarResult(next(self.results))


def test_unguarded_commit_locks_authoritative_source_policy() -> None:
    conn = RecordingConnection([True])
    registry = SourceRegistryRepository(conn)  # type: ignore[arg-type]
    assert registry.authorized_for_commit('source')
    assert len(conn.queries) == 1
    sql, params = conn.queries[0]
    assert 'FOR SHARE' in sql
    assert 'permission_status' in sql and 'discovery_enabled' in sql
    assert 'detail_extraction_enabled' in sql and 'ingestion_mode' in sql
    assert params == {'id': 'source'}


@pytest.mark.parametrize('state', [False, None])
def test_closed_source_never_reaches_lease_check(state: object) -> None:
    conn = RecordingConnection([state])
    registry = SourceRegistryRepository(conn)  # type: ignore[arg-type]
    assert not registry.authorized_for_commit('source', lease_token='lease')
    assert len(conn.queries) == 1


@pytest.mark.parametrize('lease', [False, None])
def test_missing_or_expired_lease_blocks_commit(lease: object) -> None:
    conn = RecordingConnection([True, lease])
    registry = SourceRegistryRepository(conn)  # type: ignore[arg-type]
    assert not registry.authorized_for_commit('source', lease_token='lease')
    assert len(conn.queries) == 2
    sql, params = conn.queries[1]
    assert 'FOR SHARE' in sql and 'clock_timestamp()' in sql
    assert 'lease_token=CAST(:token AS uuid)' in sql
    assert params == {'id': 'source', 'token': 'lease'}


def test_authorized_lease_fences_source_and_operational_state() -> None:
    conn = RecordingConnection([True, True])
    registry = SourceRegistryRepository(conn)  # type: ignore[arg-type]
    assert registry.authorized_for_commit('source', lease_token='lease')
    assert all('FOR SHARE' in sql for sql, _ in conn.queries)


def test_preflight_lease_uses_wall_clock_not_transaction_start() -> None:
    from pathlib import Path

    file = Path(__file__).resolve().parents[1] / 'src/hackathon_sources/registry.py'
    source = file.read_text(encoding='utf-8')
    query = source.split('def has_current_lease', 1)[1].split('def authorized_for_commit', 1)[0]
    assert 'clock_timestamp()' in query
    assert 'lease_until > now()' not in query
