from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from hackathon_api.db import connection_dependency
from hackathon_api.repositories.admin import AdminRepository
from hackathon_api.security import (
    AdminPrincipal,
    admin_operator,
    admin_principal,
    admin_superuser,
    require_admin,
)
from hackathon_shared.settings import get_settings
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
ConnectionDep = Annotated[Connection, Depends(connection_dependency)]
ViewerDep = Annotated[AdminPrincipal, Depends(admin_principal)]
OperatorDep = Annotated[AdminPrincipal, Depends(admin_operator)]
SuperuserDep = Annotated[AdminPrincipal, Depends(admin_superuser)]


class AdminAction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=8, max_length=500)


class SourceActiveUpdate(AdminAction):
    active: bool


class ReviewTransition(AdminAction):
    state: Literal["IN_REVIEW", "RESOLVED", "DISMISSED"]
    assigned_to_user_id: UUID | None = None


def request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("/me")
def admin_identity(
    request: Request,
    response: Response,
    principal: ViewerDep,
) -> dict[str, Any]:
    """Browser bootstrap for admin sessions; never return the HttpOnly session secret.

    The CSRF cookie is deliberately non-HttpOnly and only echoed for an authenticated
    USER_SESSION. This allows same-site cross-subdomain deployments, where the web
    host cannot inspect the API host's cookie directly. CORS must explicitly trust the
    web origin and allow credentials; do not use a wildcard origin.
    """
    response.headers["Cache-Control"] = "no-store"
    csrf_token = (
        request.cookies.get(get_settings().csrf_cookie_name)
        if principal.actor_kind == "USER_SESSION"
        else None
    )
    return {
        "role": principal.role,
        "actor_kind": principal.actor_kind,
        "csrf_token": csrf_token,
    }


@router.get("/coverage")
def coverage(
    principal: ViewerDep,
    connection: ConnectionDep,
    recent_days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    """Database-backed coverage; never confuse source configuration with events found."""
    from hackathon_api.repositories.coverage import CoverageRepository

    return CoverageRepository(connection).snapshot(recent_days=recent_days)


@router.get("/coverage/opportunities")
def source_opportunities(
    principal: ViewerDep,
    connection: ConnectionDep,
    recent_days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=100, ge=1, le=100),
) -> dict[str, Any]:
    """Compare source-exclusive canonical coverage and metadata gaps (read-only)."""
    from hackathon_api.repositories.source_opportunities import SourceOpportunityRepository

    return SourceOpportunityRepository(connection).snapshot(days=recent_days, limit=limit)


@router.get("/canonical-yield")
def canonical_yield(
    principal: ViewerDep,
    connection: ConnectionDep,
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    """Read-only per-crawl canonical outcomes, preserving unattributed legacy records."""
    from hackathon_api.repositories.canonical_yield import CanonicalYieldRepository

    return CanonicalYieldRepository(connection).snapshot(days=days)


@router.get("/source-insights")
def source_insights(
    principal: ViewerDep,
    connection: ConnectionDep,
    recent_days: int = Query(default=7, ge=1, le=90),
    run_limit: int = Query(default=40, ge=1, le=200),
) -> dict[str, Any]:
    """Historical crawl health and redacted source triage (read-only)."""
    from hackathon_api.repositories.source_insights import SourceInsightsRepository

    return SourceInsightsRepository(connection).snapshot(
        recent_days=recent_days,
        run_limit=run_limit,
    )


@router.get("/review-queue")
def review_queue(
    principal: ViewerDep,
    connection: ConnectionDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return AdminRepository(connection).review_queue(limit=limit)


@router.get("/source-health")
def source_health(principal: ViewerDep, connection: ConnectionDep) -> list[dict[str, Any]]:
    return AdminRepository(connection).source_health()


@router.get("/quality")
def quality(
    principal: ViewerDep,
    connection: ConnectionDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return AdminRepository(connection).latest_quality(limit=limit)


@router.get("/audit")
def audit_events(
    principal: ViewerDep,
    connection: ConnectionDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return AdminRepository(connection).audit_events(limit=limit)


@router.patch("/sources/{source_id}/active")
def update_source_active(
    source_id: UUID,
    payload: SourceActiveUpdate,
    request: Request,
    principal: OperatorDep,
    connection: ConnectionDep,
) -> dict[str, Any]:
    result = AdminRepository(connection).change_source_active(
        source_id=str(source_id),
        active=payload.active,
        reason=payload.reason,
        principal=principal,
        request_id=request_id(request),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="source not found")
    return result


@router.patch("/review-queue/{review_id}")
def transition_review(
    review_id: UUID,
    payload: ReviewTransition,
    request: Request,
    principal: OperatorDep,
    connection: ConnectionDep,
) -> dict[str, Any]:
    try:
        result = AdminRepository(connection).transition_review(
            review_id=str(review_id),
            target_state=payload.state,
            assigned_to_user_id=(
                str(payload.assigned_to_user_id) if payload.assigned_to_user_id else None
            ),
            reason=payload.reason,
            principal=principal,
            request_id=request_id(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="review item not found")
    return result


@router.post("/search/reindex")
def rebuild_search_index(
    payload: AdminAction,
    request: Request,
    principal: SuperuserDep,
    connection: ConnectionDep,
) -> dict[str, int]:
    count = int(
        connection.execute(text("SELECT rebuild_hackathon_search_documents()")).scalar_one()
    )
    AdminRepository(connection).audit_reindex(
        count=count,
        principal=principal,
        reason=payload.reason,
        request_id=request_id(request),
    )
    return {"indexed": count}
