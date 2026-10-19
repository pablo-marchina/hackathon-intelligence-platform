from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import Connection, text

from hackathon_api.auth import hash_secret
from hackathon_api.db import connection_dependency
from hackathon_api.repositories.auth import AuthRepository
from hackathon_api.schemas import AuthUser
from hackathon_shared.settings import get_settings


@dataclass(frozen=True)
class SessionContext:
    user: AuthUser
    token_hash: str
    csrf_hash: str


@dataclass(frozen=True)
class AdminPrincipal:
    actor_user_id: str | None
    actor_kind: Literal["API_KEY", "USER_SESSION"]
    role: Literal["VIEWER", "OPERATOR", "ADMIN"]
    csrf_hash: str | None = None


def _valid_admin_api_key(value: str | None) -> bool:
    expected = get_settings().admin_api_key
    return bool(expected and value is not None and hmac.compare_digest(value, expected))


def require_admin(request: Request, x_admin_key: str | None = Header(default=None)) -> None:
    """Cheap authentication preflight, executed before database-dependent route handlers."""
    settings = get_settings()
    if x_admin_key is not None:
        if not settings.admin_api_key:
            raise HTTPException(status_code=503, detail="admin endpoints are disabled")
        if not _valid_admin_api_key(x_admin_key):
            raise HTTPException(status_code=401, detail="invalid admin key")
        return
    if request.cookies.get(settings.session_cookie_name):
        return  # Authorization is checked by admin_principal using the database.
    if not settings.admin_api_key:
        raise HTTPException(status_code=503, detail="admin endpoints are disabled")
    raise HTTPException(status_code=401, detail="admin credentials required")


def current_session(
    request: Request, connection: Annotated[Connection, Depends(connection_dependency)]
) -> SessionContext:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    token_hash = hash_secret(token)
    row = AuthRepository(connection).session_user(token_hash)
    if not row:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    user, csrf_hash = row
    return SessionContext(user, token_hash, csrf_hash)


def _check_csrf(request: Request, csrf_hash: str, token: str | None) -> None:
    cookie = request.cookies.get(get_settings().csrf_cookie_name)
    if (
        not cookie
        or not token
        or not hmac.compare_digest(cookie, token)
        or not hmac.compare_digest(hash_secret(token), csrf_hash)
    ):
        raise HTTPException(status_code=403, detail="invalid csrf token")


def require_csrf(
    request: Request,
    session: Annotated[SessionContext, Depends(current_session)],
    x_csrf_token: str | None = Header(default=None),
) -> SessionContext:
    _check_csrf(request, session.csrf_hash, x_csrf_token)
    return session


def admin_principal(
    request: Request,
    connection: Annotated[Connection, Depends(connection_dependency)],
    x_admin_key: str | None = Header(default=None),
) -> AdminPrincipal:
    require_admin(request, x_admin_key)
    if x_admin_key is not None:
        return AdminPrincipal(None, "API_KEY", "ADMIN")
    session = current_session(request, connection)
    role = connection.execute(
        text("SELECT role FROM admin_user_roles WHERE user_id = :user_id"),
        {"user_id": session.user.id},
    ).scalar_one_or_none()
    if role not in {"VIEWER", "OPERATOR", "ADMIN"}:
        raise HTTPException(status_code=403, detail="admin role required")
    return AdminPrincipal(str(session.user.id), "USER_SESSION", role, session.csrf_hash)


def _authorized_action(
    principal: AdminPrincipal,
    request: Request,
    x_csrf_token: str | None,
    allowed_roles: set[str],
) -> AdminPrincipal:
    if principal.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="insufficient admin role")
    if principal.actor_kind == "USER_SESSION":
        assert principal.csrf_hash is not None
        _check_csrf(request, principal.csrf_hash, x_csrf_token)
    return principal


def admin_operator(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(admin_principal)],
    x_csrf_token: str | None = Header(default=None),
) -> AdminPrincipal:
    return _authorized_action(principal, request, x_csrf_token, {"OPERATOR", "ADMIN"})


def admin_superuser(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(admin_principal)],
    x_csrf_token: str | None = Header(default=None),
) -> AdminPrincipal:
    return _authorized_action(principal, request, x_csrf_token, {"ADMIN"})
