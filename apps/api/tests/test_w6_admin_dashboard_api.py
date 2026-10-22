"""Browser dashboard bootstrap and least-privilege API contract."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import hackathon_api.security as security
import pytest
from fastapi.testclient import TestClient
from hackathon_api.auth import hash_secret
from hackathon_api.db import connection_dependency
from hackathon_api.main import app
from hackathon_shared.settings import Settings


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def mappings(self):
        return self

    def all(self):
        return self.value


class DB:
    role = "VIEWER"

    def execute(self, statement, params=None):
        if "SELECT role FROM admin_user_roles" in str(statement):
            return Result(self.role)
        return Result([])


@pytest.fixture
def client(monkeypatch):
    db = DB()
    monkeypatch.setattr(security, "get_settings", lambda: Settings(admin_api_key="K" * 48))
    monkeypatch.setattr(
        "hackathon_api.routes.admin.get_settings", lambda: Settings(admin_api_key="K" * 48)
    )
    app.dependency_overrides[connection_dependency] = lambda: db
    try:
        with TestClient(app) as http:
            yield http, db
    finally:
        app.dependency_overrides.clear()


def test_service_bootstrap_does_not_leak_api_key_or_csrf(client):
    http, db = client
    response = http.get("/admin/me", headers={"X-Admin-Key": "K" * 48})
    assert response.status_code == 200
    assert response.json() == {"role": "ADMIN", "actor_kind": "API_KEY", "csrf_token": None}
    assert "no-store" in response.headers["cache-control"]
    assert "K" * 48 not in response.text
    assert http.get("/admin/me", headers={"X-Admin-Key": "wrong"}).status_code == 401


def test_session_bootstrap_respects_db_role_and_reflects_only_csrf(client, monkeypatch):
    http, db = client
    monkeypatch.setattr(
        security,
        "current_session",
        lambda request, connection: security.SessionContext(
            SimpleNamespace(id=UUID("00000000-0000-4000-8000-000000000013")),
            "private-session-hash",
            hash_secret("valid-csrf-token"),
        ),
    )
    http.cookies.set("hip_session", "opaque-session-cookie")
    http.cookies.set("hip_csrf", "valid-csrf-token")
    for role in ("VIEWER", "OPERATOR", "ADMIN"):
        db.role = role
        response = http.get("/admin/me")
        assert response.status_code == 200
        assert response.json() == {
            "role": role,
            "actor_kind": "USER_SESSION",
            "csrf_token": "valid-csrf-token",
        }
        assert "opaque-session-cookie" not in response.text
        assert "private-session-hash" not in response.text
    db.role = None
    assert http.get("/admin/me").status_code == 403


def test_admin_me_is_not_anonymous(client):
    http, _ = client
    assert http.get("/admin/me").status_code == 401
