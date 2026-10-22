from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from hackathon_api.db import connection_dependency
from hackathon_api.repositories.catalog import CatalogRepository
from hackathon_api.schemas import (
    EventSeriesDetail,
    OrganizationDetail,
    SourceListItem,
    TaxonomyItem,
)
from sqlalchemy import Connection

router = APIRouter(tags=["catalog"])


@router.get("/organizations")
def organizations(
    connection: Annotated[Connection, Depends(connection_dependency)],
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
) -> dict[str, Any]:
    items, n = CatalogRepository(connection).organizations(limit, cursor)
    return {"items": items, "next_cursor": n, "api_version": "v1"}


@router.get("/organizations/{slug}", response_model=OrganizationDetail)
def organization(slug: str, connection: Annotated[Connection, Depends(connection_dependency)]):
    i = CatalogRepository(connection).organization(slug)
    if not i:
        raise HTTPException(status_code=404, detail="organization not found")
    return i


@router.get("/series")
def series_list(
    connection: Annotated[Connection, Depends(connection_dependency)],
    limit: int = Query(100, ge=1, le=200),
):
    return {"items": CatalogRepository(connection).list_series(limit), "api_version": "v1"}


@router.get("/series/{slug}", response_model=EventSeriesDetail)
def series_detail(slug: str, connection: Annotated[Connection, Depends(connection_dependency)]):
    i = CatalogRepository(connection).series(slug)
    if not i:
        raise HTTPException(status_code=404, detail="series not found")
    return i


@router.get("/tracks", response_model=list[TaxonomyItem])
def tracks(connection: Annotated[Connection, Depends(connection_dependency)]):
    return CatalogRepository(connection).taxonomy("tracks")


@router.get("/technologies", response_model=list[TaxonomyItem])
def technologies(connection: Annotated[Connection, Depends(connection_dependency)]):
    return CatalogRepository(connection).taxonomy("technologies")


@router.get("/sources", response_model=list[SourceListItem])
def sources(
    connection: Annotated[Connection, Depends(connection_dependency)],
    limit: int = Query(200, ge=1, le=500),
):
    return CatalogRepository(connection).sources(limit)
