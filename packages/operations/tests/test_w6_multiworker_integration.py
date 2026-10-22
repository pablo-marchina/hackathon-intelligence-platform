"""Unit-level guards for W6 concurrency and isolated Celery/Redis CI probes."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from hackathon_operations.source_maintenance import (
    LEASE_DURATION,
    claim_due_source,
    complete_lease,
    renew_lease,
)

ROOT = Path(__file__).resolve().parents[3]


def load_check(filename: str) -> ModuleType:
    path = ROOT / "scripts" / "ops" / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Result:
    def mappings(self) -> Result:
        return self

    def first(self):
        return None


class Connection:
    def __init__(self) -> None:
        self.sql = ""
        self.params: dict[str, object] = {}

    def execute(self, stmt: object, params: dict[str, object]) -> Result:
        self.sql = str(stmt)
        self.params = params
        return Result()


@pytest.mark.parametrize("operation", ["claim", "renew", "complete"])
def test_production_lease_operations_use_one_database_clock(operation: str) -> None:
    conn = Connection()
    if operation == "claim":
        claim_due_source(conn, allowed_sources=[("example.test", "example")])
    elif operation == "renew":
        renew_lease(conn, source_id="source", lease_token="token")
    else:
        complete_lease(conn, source_id="source", lease_token="token", success=False)
    assert "WITH clock AS MATERIALIZED" in conn.sql
    assert "clock_timestamp()" in conn.sql
    assert "clock.tick" in conn.sql
    assert conn.params["now"] is None, "production must not send its own wall clock"
    assert "lease_until > clock.tick" in conn.sql or "lease_until <= clock.tick" in conn.sql


def test_db_clock_claim_sets_ttl_and_keeps_skip_locked() -> None:
    conn = Connection()
    assert claim_due_source(conn, allowed_sources=[("a.test", "ci")]) is None
    assert "FOR UPDATE OF st SKIP LOCKED LIMIT 1" in conn.sql
    assert "lease_until=clock.tick + make_interval(secs => :lease_seconds)" in conn.sql
    assert conn.params["lease_seconds"] == int(LEASE_DURATION.total_seconds())


def test_db_clock_cannot_complete_stale_lease() -> None:
    conn = Connection()
    assert not complete_lease(conn, source_id="source", lease_token="old", success=True)
    assert "st.lease_token=CAST(:token AS uuid)" in conn.sql
    assert "st.lease_until > clock.tick" in conn.sql
    assert "updated_at=clock.tick" in conn.sql


def test_multiworker_drill_refuses_unapproved_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_check("source_multiworker_integration_check.py")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("HIP_ALLOW_SOURCE_DB_DRILL", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ci:ci@127.0.0.1:5432/hackathon")
    with pytest.raises(RuntimeError, match="non-isolated"):
        module._allowed()


def test_multiworker_drill_rejects_remote_host(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_check("source_multiworker_integration_check.py")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("HIP_ALLOW_SOURCE_DB_DRILL", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ci:ci@database.example:5432/hackathon")
    with pytest.raises(RuntimeError, match="loopback"):
        module._allowed()


def isolated_redis_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("HIP_ALLOW_CELERY_DRILL", "1")
    monkeypatch.setenv("SOURCE_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/13")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/14")


def test_celery_smoke_requires_isolated_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_check("celery_redis_integration_check.py")
    isolated_redis_env(monkeypatch)
    module._allowed("hip-ci-smoke")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://redis.internal:6379/13")
    with pytest.raises(RuntimeError, match="isolated local Redis"):
        module._allowed("hip-ci-smoke")
    isolated_redis_env(monkeypatch)
    monkeypatch.setenv("SOURCE_SCHEDULER_ENABLED", "true")
    with pytest.raises(RuntimeError, match="disabled"):
        module._allowed("hip-ci-smoke")


def test_celery_smoke_never_sends_operational_task(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_check("celery_redis_integration_check.py")
    isolated_redis_env(monkeypatch)

    class FakeReply:
        status = "SUCCESS"
        forgotten = False

        def get(self, **kwargs):
            assert kwargs == {"timeout": 40, "propagate": True}
            return "pong"

        def forget(self):
            self.forgotten = True

    class FakeApp:
        def __init__(self):
            self.reply = FakeReply()
            self.calls = []

        def send_task(self, name, **kwargs):
            self.calls.append((name, kwargs))
            return self.reply

    app = FakeApp()
    task_id = module.verify(app=app)
    assert task_id.startswith("hip-ci-ping-")
    assert app.calls[0][0] == "system.ping"
    assert app.calls[0][1]["queue"] == "hip-ci-smoke"
    assert app.calls[0][1]["expires"] == 60
    assert app.reply.forgotten


def test_lease_clock_override_is_explicit_for_deterministic_tests() -> None:
    fixed = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
    conn = Connection()
    claim_due_source(conn, allowed_sources=[("example.test", "test")], now=fixed)
    assert conn.params["now"] == fixed
    renew_lease(conn, source_id="s", lease_token="t", now=fixed)
    assert conn.params["now"] == fixed
    complete_lease(conn, source_id="s", lease_token="t", now=fixed, success=True)
    assert conn.params["now"] == fixed


def test_dispatcher_does_not_supply_worker_clock_to_lease_completion() -> None:
    source = (ROOT / "packages/operations/src/hackathon_operations/dispatcher.py").read_text()
    lease_completion = source[source.index("released = complete_lease(") :]
    assert "now=datetime.now" not in lease_completion
    assert "success=ok" in lease_completion


def test_ci_runs_new_service_drills() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "source_multiworker_integration_check.py" in workflow
    assert "celery-redis-integration:" in workflow
    assert "celery_redis_integration_check.py" in workflow
    assert 'SOURCE_SCHEDULER_ENABLED: "false"' in workflow


def test_multiworker_guard_rejects_driver_redirect_options(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_check("source_multiworker_integration_check.py")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("HIP_ALLOW_SOURCE_DB_DRILL", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ci:ci@127.0.0.1/hackathon?host=remote")
    with pytest.raises(RuntimeError, match="URL options"):
        module._allowed()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ci:ci@127.0.0.1/hackathon")
    monkeypatch.setenv("PGHOSTADDR", "10.0.0.8")
    with pytest.raises(RuntimeError, match="PGHOSTADDR"):
        module._allowed()
