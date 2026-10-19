"""Readiness reflects database availability AND migration state."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError, ProgrammingError

import hackathon_api.main as main
from hackathon_api.main import app


class Connection:
    def __init__(self, version=None, error=None):
        self.version = version
        self.error = error
        self.sql = []

    def execute(self, statement):
        self.sql.append(str(statement))
        if self.error:
            raise self.error
        return self

    def scalar_one_or_none(self):
        return self.version


class Engine:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection


def test_migrated_database_is_ready(monkeypatch):
    conn = Connection(main.EXPECTED_DB_REVISION)
    monkeypatch.setattr(main, "get_engine", lambda: Engine(conn))
    response = TestClient(app).get("/ready")
    assert response.status_code == 200 and response.json() == {"status": "ready"}
    assert "alembic_version" in conn.sql[0]


def test_missing_or_outdated_schema_not_ready(monkeypatch):
    for value in (None, "0007_w6_source_operations"):
        monkeypatch.setattr(main, "get_engine", lambda: Engine(Connection(value)))
        response = TestClient(app).get("/ready")
        assert response.status_code == 503
        assert response.json() == {"detail": "service not ready"}


def test_unavailable_database_does_not_expose_error_details(monkeypatch):
    error = OperationalError("SELECT 1", {}, Exception("private-database.internal"))
    monkeypatch.setattr(main, "get_engine", lambda: Engine(Connection(error=error)))
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert "private-database.internal" not in response.text
    assert TestClient(app).get("/health").status_code == 200


def test_bad_schema_query_not_ready(monkeypatch):
    error = ProgrammingError("SELECT version_num FROM alembic_version", {}, Exception("missing"))
    monkeypatch.setattr(main, "get_engine", lambda: Engine(Connection(error=error)))
    assert TestClient(app).get("/ready").status_code == 503


def test_expected_revision_matches_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[3]
    script = ScriptDirectory.from_config(Config(str(root / "db/alembic.ini")))
    assert script.get_current_head() == main.EXPECTED_DB_REVISION
