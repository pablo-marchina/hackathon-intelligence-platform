"""Ensure the remote diagnostic and CLI cannot silently bypass source switches."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _diagnostic_module():
    spec = importlib.util.spec_from_file_location('live_sources_diagnostic', ROOT / 'api/live_sources.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_diagnostic_requires_secret_including_if_unconfigured(monkeypatch):
    module = _diagnostic_module()
    captured = []
    diagnostic = module.handler.__new__(module.handler)
    diagnostic.headers = {'x-gate-token': 'incorrect'}
    diagnostic._json = lambda status, payload: captured.append((status, payload))
    monkeypatch.delenv('LIVE_SOURCES_GATE_TOKEN', raising=False)
    diagnostic.do_GET()
    assert captured[-1][0] == 503
    monkeypatch.setenv('LIVE_SOURCES_GATE_TOKEN', 'correct')
    diagnostic.do_GET()
    assert captured[-1][0] == 401


def test_api_diagnostic_blocks_unmanaged_fetch_in_production(monkeypatch):
    module = _diagnostic_module()
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('LIVE_SOURCES_ALLOW_UNMANAGED_FETCH', '1')
    monkeypatch.setenv('APP_ENV', 'production')
    with pytest.raises(PermissionError):
        asyncio.run(module._run(set(), max_pages=1))


def test_cli_unmanaged_mode_requires_explicit_opt_in():
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(ROOT / 'packages/source_sdk/src'), str(ROOT / 'packages/shared/src')])}
    env.pop('DATABASE_URL', None)
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/run_live_sources.py'), '--no-persist', '--source', 'hackclub_hackathons'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert '--allow-unmanaged-fetch' in result.stderr
    assert 'fetched_pages' not in result.stdout


def test_cli_opt_in_is_rejected_in_staging():
    env = {**os.environ, 'APP_ENV': 'staging', 'PYTHONPATH': os.pathsep.join([str(ROOT / 'packages/source_sdk/src'), str(ROOT / 'packages/shared/src')])}
    env.pop('DATABASE_URL', None)
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/run_live_sources.py'), '--no-persist', '--allow-unmanaged-fetch', '--source', 'hackclub_hackathons'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert 'forbidden in staging and production' in result.stderr


def test_cli_rest_transport_uses_postgres_pause_guard(monkeypatch, capsys):
    """A selected REST backend must never bypass a known PostgreSQL pause."""
    import importlib.util
    from hackathon_sources.models import (
        IngestionMode,
        PermissionStatus,
        SourceDefinition,
    )

    definition = SourceDefinition(
        key='api_controlled', name='Controlled source', domain='api.example.com',
        source_family='controlled_api', source_type='api',
        ingestion_mode=IngestionMode.API,
        permission_status=PermissionStatus.ALLOWED,
        detail_extraction_enabled=True,
        seed_urls=['https://api.example.com/events'],
    )
    spec = importlib.util.spec_from_file_location('cli_live_sources', ROOT / 'scripts/run_live_sources.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = type('Engine', (), {'dispose': lambda self: None})()
    sink_calls = []
    check_calls = []

    class Sink:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def insert(self, table, row):
            sink_calls.append(table)
            return row

    class Fetcher:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def fetch(self, *args):
            raise AssertionError('paused source must not be fetched')

    monkeypatch.setattr(module, 'load_source_definitions', lambda path: [definition])
    monkeypatch.setattr(module, 'build_adapter', lambda item: _AdapterForGate(item))
    monkeypatch.setattr(module, 'create_engine', lambda *args, **kwargs: engine)
    monkeypatch.setattr(module, 'check_postgres_active', lambda received_engine, source: (
        check_calls.append((received_engine, source.key)) or False
    ))
    monkeypatch.setattr(module, 'RestrictedGateClient', lambda *args: Sink())
    monkeypatch.setattr(module, 'FetchEngine', lambda: Fetcher())
    monkeypatch.setattr(sys, 'argv', ['run_live_sources.py', '--prefer-rest', '--source', 'api_controlled'])
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://example:secret@localhost/db')
    monkeypatch.setenv('GATE_WRITE_URL', 'https://gate.example.com/write')
    monkeypatch.setenv('GATE_TOKEN', 'secret')
    monkeypatch.delenv('SUPABASE_URL', raising=False)
    monkeypatch.delenv('SUPABASE_ANON_KEY', raising=False)

    result = asyncio.run(module.main())
    assert result == 0
    assert '"SKIPPED_PAUSED"' in capsys.readouterr().out
    assert check_calls == [(engine, 'api_controlled')]
    assert sink_calls == []


class _AdapterForGate:
    def __init__(self, definition):
        self.definition = definition

    async def discover(self):
        raise AssertionError('paused source must not discover')
        yield  # pragma: no cover
