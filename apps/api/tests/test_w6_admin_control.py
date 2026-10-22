"""Local security and repository tests (no PostgreSQL service required)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import hackathon_api.security as security
import pytest
from fastapi.testclient import TestClient
from hackathon_api.auth import hash_secret
from hackathon_api.db import connection_dependency
from hackathon_api.main import app
from hackathon_api.repositories.admin import AdminRepository
from hackathon_api.security import AdminPrincipal
from hackathon_shared.settings import Settings

SOURCE_ID = "00000000-0000-4000-8000-000000000001"
REVIEW_ID = "00000000-0000-4000-8000-000000000002"
USER_ID = "00000000-0000-4000-8000-000000000003"


class Result:
    def __init__(self, value=None):
        self.value = value

    def mappings(self):
        return self

    def first(self):
        return self.value

    def all(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class FakeConnection:
    def __init__(self):
        self.source = {"id": SOURCE_ID, "active": True}
        self.review = {"id": REVIEW_ID, "state": "OPEN", "assigned_to_user_id": None}
        self.audit = []
        self.executions = []
        self.role = "VIEWER"

    def execute(self, statement, params=None):
        sql = str(statement).strip()
        params = params or {}
        self.executions.append((sql, dict(params)))
        if "SELECT role FROM admin_user_roles" in sql:
            return Result(self.role)
        if "SELECT id::text AS id, active FROM sources" in sql:
            return Result(dict(self.source) if params["id"] == SOURCE_ID else None)
        if "UPDATE sources SET active" in sql:
            self.source["active"] = params["active"]
        if "SELECT id::text AS id, state::text AS state" in sql:
            return Result(dict(self.review) if params["id"] == REVIEW_ID else None)
        if "UPDATE review_queue_items" in sql:
            self.review.update(state=params["state"], assigned_to_user_id=params["assignee"])
        if "INSERT INTO admin_audit_log" in sql:
            self.audit.append(dict(params))
        if "rebuild_hackathon_search_documents()" in sql:
            return Result(17)
        if "FROM admin_audit_log ORDER BY" in sql:
            return Result(list(reversed(self.audit)))
        if "FROM sources" in sql:
            return Result([dict(self.source)])
        return Result([])


@pytest.fixture
def api(monkeypatch):
    conn = FakeConnection()
    settings = Settings(admin_api_key="a" * 40)
    monkeypatch.setattr(security, "get_settings", lambda: settings)

    def fake_db():
        yield conn

    app.dependency_overrides[connection_dependency] = fake_db
    try:
        yield TestClient(app), conn
    finally:
        app.dependency_overrides.clear()


def test_machine_admin_auth_failure_and_authorization(api):
    client, conn = api
    assert client.get("/admin/source-health").status_code == 401
    assert client.get("/admin/source-health", headers={"x-admin-key": "wrong"}).status_code == 401
    assert client.get("/admin/source-health", headers={"x-admin-key": "a" * 40}).status_code == 200
    assert not conn.audit


def test_pause_resume_is_idempotent_and_audited(api):
    client, conn = api
    url = f"/admin/sources/{SOURCE_ID}/active"
    headers = {"x-admin-key": "a" * 40}
    assert (
        client.patch(url, json={"active": False, "reason": "short"}, headers=headers).status_code
        == 422
    )
    pause = client.patch(
        url, json={"active": False, "reason": "source disabled by operator"}, headers=headers
    )
    assert pause.status_code == 200
    assert pause.json() == {"id": SOURCE_ID, "active": False, "changed": True}
    assert len(conn.audit) == 1
    assert conn.audit[0]["actor_kind"] == "API_KEY"
    assert conn.audit[0]["actor_user_id"] is None
    assert conn.audit[0]["action"] == "SOURCE_PAUSE"
    assert "a" * 40 not in repr(conn.audit)
    assert (
        client.patch(
            url, json={"active": True, "reason": "           "}, headers=headers
        ).status_code
        == 422
    )
    again = client.patch(
        url, json={"active": False, "reason": "repeated pause requested"}, headers=headers
    )
    assert again.json()["changed"] is False
    assert len(conn.audit) == 1
    resume = client.patch(
        url, json={"active": True, "reason": "source verified and resumed"}, headers=headers
    )
    assert resume.json()["active"] is True
    assert [a["action"] for a in conn.audit] == ["SOURCE_PAUSE", "SOURCE_RESUME"]
    assert (
        client.patch(
            f"/admin/sources/{USER_ID}/active",
            json={"active": False, "reason": "missing source record"},
            headers=headers,
        ).status_code
        == 404
    )


def test_review_lifecycle_rejects_invalid_transitions(api):
    client, conn = api
    headers = {"x-admin-key": "a" * 40}
    url = f"/admin/review-queue/{REVIEW_ID}"
    change = client.patch(
        url, json={"state": "IN_REVIEW", "reason": "manual triage started"}, headers=headers
    )
    assert change.status_code == 200
    assert change.json()["state"] == "IN_REVIEW"
    resolved = client.patch(
        url, json={"state": "RESOLVED", "reason": "evidence confirmed by operator"}, headers=headers
    )
    assert resolved.status_code == 200
    assert resolved.json()["state"] == "RESOLVED"
    forbidden = client.patch(
        url, json={"state": "DISMISSED", "reason": "reopening a closed review"}, headers=headers
    )
    assert forbidden.status_code == 409
    assert len(conn.audit) == 2
    assert all(a["action"] == "REVIEW_TRANSITION" for a in conn.audit)


def test_review_assignee_requires_operator_role(api):
    client, conn = api
    conn.role = "VIEWER"
    headers = {"x-admin-key": "a" * 40}
    response = client.patch(
        f"/admin/review-queue/{REVIEW_ID}",
        json={
            "state": "IN_REVIEW",
            "reason": "assigned for triage",
            "assigned_to_user_id": USER_ID,
        },
        headers=headers,
    )
    assert response.status_code == 409
    assert conn.review["state"] == "OPEN"
    assert not conn.audit


def test_reindex_requires_reason_and_creates_audit(api):
    client, conn = api
    headers = {"x-admin-key": "a" * 40}
    assert client.post("/admin/search/reindex", headers=headers).status_code == 422
    response = client.post(
        "/admin/search/reindex", json={"reason": "requested index refresh"}, headers=headers
    )
    assert response.status_code == 200 and response.json() == {"indexed": 17}
    assert conn.audit[0]["action"] == "SEARCH_REINDEX"


def test_session_rbac_and_csrf(api, monkeypatch):
    client, conn = api
    user = SimpleNamespace(id=UUID(USER_ID))
    monkeypatch.setattr(
        security,
        "current_session",
        lambda request, connection: security.SessionContext(
            user, "opaque", hash_secret("csrf-secret")
        ),
    )
    client.cookies.set("hip_session", "session-value")
    url = f"/admin/sources/{SOURCE_ID}/active"
    action = {"active": False, "reason": "source manual moderation"}
    assert client.get("/admin/source-health").status_code == 200
    client.cookies.set("hip_csrf", "csrf-secret")
    assert (
        client.patch(url, json=action, headers={"x-csrf-token": "csrf-secret"}).status_code == 403
    )
    conn.role = "OPERATOR"
    assert client.patch(url, json=action).status_code == 403  # missing CSRF header
    assert client.patch(url, json=action, headers={"x-csrf-token": "bad"}).status_code == 403
    changed = client.patch(url, json=action, headers={"x-csrf-token": "csrf-secret"})
    assert changed.status_code == 200
    assert conn.audit[0]["actor_kind"] == "USER_SESSION"
    assert conn.audit[0]["actor_user_id"] == USER_ID
    assert (
        client.post(
            "/admin/search/reindex",
            json={"reason": "review of index drift"},
            headers={"x-csrf-token": "csrf-secret"},
        ).status_code
        == 403
    )
    conn.role = "ADMIN"
    assert (
        client.post(
            "/admin/search/reindex",
            json={"reason": "review of index drift"},
            headers={"x-csrf-token": "csrf-secret"},
        ).status_code
        == 200
    )
    conn.role = None
    assert client.get("/admin/source-health").status_code == 403


def test_admin_uses_parameterized_queries_and_audit_provenance():
    conn = FakeConnection()
    repo = AdminRepository(conn)
    principal = AdminPrincipal(USER_ID, "USER_SESSION", "OPERATOR")
    result = repo.change_source_active(
        source_id=SOURCE_ID,
        active=False,
        reason="investigating source",
        principal=principal,
        request_id="req_123",
    )
    assert result and result["changed"]
    assert conn.audit[0]["request_id"] == "req_123"
    assert conn.audit[0]["before"] == '{"active": true}'
    assert conn.audit[0]["after"] == '{"active": false}'
    assert all(SOURCE_ID not in statement for statement, _ in conn.executions)


def test_openapi_includes_w6_mutation_contract():
    paths = app.openapi()["paths"]
    assert {
        "/admin/audit",
        "/admin/sources/{source_id}/active",
        "/admin/review-queue/{review_id}",
    } <= set(paths)
