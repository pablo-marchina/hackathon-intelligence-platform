from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hackathon_operations.source_maintenance import (
    ALERT_COOLDOWN,
    LEASE_DURATION,
    _recent_run_sample,
    build_source_plan,
)
from hackathon_operations.regression import detect_source_regression

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def test_new_source_scheduled_immediately_without_network_fetch():
    plan = build_source_plan(
        now=NOW, latest_run=None, previous_due=None, last_processed_run_id=None,
        nearest_start=None, nearest_close=None, change_probability=0,
        priority=50, trust_score=None, minimum_interval_seconds=3600,
    )
    assert plan.next_due_at == NOW
    assert plan.interval >= timedelta(hours=1)


def test_no_new_crawl_keeps_existing_overdue_slot():
    run = {'id': 'crawl-1', 'started_at': NOW - timedelta(hours=12), 'finished_at': NOW - timedelta(hours=11)}
    due = NOW - timedelta(hours=2)
    plan = build_source_plan(
        now=NOW, latest_run=run, previous_due=due, last_processed_run_id='crawl-1',
        nearest_start=NOW + timedelta(hours=8), nearest_close=None, change_probability=1,
        priority=90, trust_score=0.99, minimum_interval_seconds=3600,
    )
    assert plan.next_due_at == due


def test_new_run_replans_from_actual_completion():
    finished = NOW - timedelta(minutes=15)
    run = {'id': 'new-run', 'started_at': finished - timedelta(minutes=10), 'finished_at': finished}
    plan = build_source_plan(
        now=NOW, latest_run=run, previous_due=NOW - timedelta(days=1),
        last_processed_run_id='old-run', nearest_start=NOW + timedelta(hours=1),
        nearest_close=None, change_probability=1, priority=100,
        trust_score=1, minimum_interval_seconds=5400,
    )
    assert plan.interval == timedelta(minutes=90)
    assert plan.next_due_at == finished + timedelta(minutes=90)


def test_source_minimum_rate_limit_and_failure_backoff():
    values = dict(
        now=NOW, latest_run=None, previous_due=None, last_processed_run_id=None,
        nearest_start=NOW + timedelta(hours=1), nearest_close=None, change_probability=1,
        priority=100, trust_score=1, minimum_interval_seconds=10800,
    )
    assert build_source_plan(**values).interval == timedelta(hours=3)
    assert build_source_plan(**(values | {'consecutive_failures': 7})).interval == timedelta(days=1)


def test_failed_attempt_counts_for_regression():
    previous = _recent_run_sample({'fetched_count': 40, 'failed_count': 0, 'parsed_count': 40, 'valid_event_count': 30})
    failed = _recent_run_sample({'fetched_count': 0, 'failed_count': 30, 'parsed_count': 0, 'valid_event_count': 0})
    codes = {signal.code for signal in detect_source_regression(previous, failed)}
    assert {'PARSER_ZERO_OUTPUT', 'HIGH_FAILURE_RATE', 'EVENT_COUNT_COLLAPSE'} <= codes


def test_cooldown_lease_are_nonzero_and_bounded():
    assert ALERT_COOLDOWN == timedelta(hours=24)
    assert LEASE_DURATION == timedelta(minutes=30)


def test_migration_has_indexed_due_queue_and_fk_cleanup():
    sql = (Path(__file__).resolve().parents[3] / 'db/sql/0007_w6_source_operations.sql').read_text()
    assert 'source_operational_due_idx' in sql
    assert 'ON DELETE CASCADE' in sql
    assert 'CHECK ((lease_token IS NULL) = (lease_until IS NULL))' in sql


def test_opt_in_scheduler_defaults_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv('SOURCE_SCHEDULER_ENABLED', raising=False)
    from hackathon_shared.settings import Settings
    assert Settings(_env_file=None).source_scheduler_enabled is False


def test_scheduler_worker_does_not_schedule_when_disabled():
    worker = (Path(__file__).resolve().parents[3] / 'apps/worker/src/hackathon_worker/app.py').read_text()
    assert 'if settings.source_scheduler_enabled:' in worker
    assert 'if not config.source_scheduler_enabled:' in worker


def test_integration_guard_blocks_non_ci_database(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    path = Path(__file__).resolve().parents[3] / 'scripts/ops/source_maintenance_integration_check.py'
    spec = importlib.util.spec_from_file_location('source_drill', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('HIP_ALLOW_SOURCE_DB_DRILL', '1')
    with pytest.raises(RuntimeError, match='non-isolated'):
        module.verify()


def test_dispatcher_bootstraps_only_live_sources_before_planning():
    code = (Path(__file__).resolve().parents[1] / 'src/hackathon_operations/dispatcher.py').read_text()
    assert 'if d.key in live_adapter_keys()' in code
    assert 'registry.upsert(definition)' in code
    assert code.index('registry.upsert(definition)') < code.index('result = maintain_sources(conn)')
