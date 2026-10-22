from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from hackathon_api.advanced_filters import filter_field_catalog
from hackathon_api.db import connection_dependency, get_engine
from hackathon_api.repositories.hackathons import HackathonRepository
from hackathon_api.schemas import AdvancedHackathonFilters, HackathonDetail, HackathonFilters, HackathonPage
from sqlalchemy import Connection

router = APIRouter(prefix="/hackathons", tags=["hackathons"])


@router.get("", response_model=HackathonPage)
def list_hackathons(
    connection: Annotated[Connection, Depends(connection_dependency)],
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    event_type: Annotated[list[str] | None, Query()] = None,
    format: Annotated[list[str] | None, Query()] = None,
    registration_state: Annotated[list[str] | None, Query()] = None,
    country_code: Annotated[list[str] | None, Query()] = None,
    city: str | None = None,
    start_from: datetime | None = None,
    start_to: datetime | None = None,
    deadline_from: datetime | None = None,
    deadline_to: datetime | None = None,
    free_only: bool = False,
    min_prize_usd: float | None = None,
    max_prize_usd: float | None = None,
    track: Annotated[list[str] | None, Query()] = None,
    technology: Annotated[list[str] | None, Query()] = None,
    organizer: Annotated[list[str] | None, Query()] = None,
    sponsor: Annotated[list[str] | None, Query()] = None,
    team_size: int | None = None,
    student_required: bool | None = None,
    individual_allowed: bool | None = None,
    eligible_country: str | None = None,
    limit: int = 30,
    cursor: str | None = None,
    sort: str = "relevance",
) -> HackathonPage:
    try:
        return HackathonRepository(connection).search(
            HackathonFilters(
                q=q,
                status=status or [],
                event_type=event_type or [],
                format=format or [],
                registration_state=registration_state or [],
                country_code=country_code or [],
                city=city,
                start_from=start_from,
                start_to=start_to,
                deadline_from=deadline_from,
                deadline_to=deadline_to,
                free_only=free_only,
                min_prize_usd=min_prize_usd,
                max_prize_usd=max_prize_usd,
                track=track or [],
                technology=technology or [],
                organizer=organizer or [],
                sponsor=sponsor or [],
                team_size=team_size,
                student_required=student_required,
                individual_allowed=individual_allowed,
                eligible_country=eligible_country,
                limit=limit,
                cursor=cursor,
                sort=sort,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/filter-fields")
def get_filter_fields() -> dict[str, object]:
    """Describe allowlisted fields and operators for the advanced filter builder."""
    return filter_field_catalog()


@router.post("/query", response_model=HackathonPage)
def query_hackathons(filters: AdvancedHackathonFilters) -> HackathonPage:
    """Validate expressions before opening the database connection."""
    try:
        with get_engine().begin() as connection:
            return HackathonRepository(connection).search(filters)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/{slug}",
    response_model=HackathonDetail,
    responses={308: {"description": "Retired canonical redirected"}},
)
def get_hackathon(
    slug: str, request: Request, connection: Annotated[Connection, Depends(connection_dependency)]
):
    repo = HackathonRepository(connection)
    item = repo.by_slug(slug)
    if item:
        return item
    active = repo.active_slug_for_retired(slug)
    if active:
        return RedirectResponse(
            url=f"{str(request.base_url).rstrip('/')}/v1/hackathons/{active}", status_code=308
        )
    raise HTTPException(status_code=404, detail="hackathon not found")
