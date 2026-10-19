from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Connection, text

from hackathon_api.db import connection_dependency
from hackathon_api.repositories.personalization import PersonalizationRepository
from hackathon_api.schemas import (
    AlertEventView,
    AlertPreferenceUpdate,
    AlertPreferenceView,
    PreferenceUpdate,
    PreferenceView,
    RecommendationPage,
    SavedSearchCreate,
    SavedSearchUpdate,
    SavedSearchView,
)
from hackathon_api.security import SessionContext, current_session, require_csrf

router = APIRouter(prefix="/me", tags=["personalization"])


@router.get("/preferences", response_model=PreferenceView)
def preferences(
    session: Annotated[SessionContext, Depends(current_session)],
    connection: Annotated[Connection, Depends(connection_dependency)],
):
    return PersonalizationRepository(connection).preference(session.user.id)


@router.put("/preferences", response_model=PreferenceView)
def update_preferences(
    payload: PreferenceUpdate,
    session: Annotated[SessionContext, Depends(require_csrf)],
    connection: Annotated[Connection, Depends(connection_dependency)],
):
    return PersonalizationRepository(connection).update_preference(session.user.id, payload)


@router.get("/recommendations", response_model=RecommendationPage)
def recommendations(
    session: Annotated[SessionContext, Depends(current_session)],
    connection: Annotated[Connection, Depends(connection_dependency)],
    limit: int = Query(default=30, ge=1, le=100),
):
    # Recommendation rows are materialized by the deterministic W5 ranker worker.
    rows = connection.execute(text("""
        SELECT r.hackathon_id,r.eligibility_result::text AS eligibility_result,r.score,r.model_version,
               r.positive_reasons,r.negative_reasons,r.components,r.input_hash,r.profile_version,
               h.id,h.slug,h.name,h.short_description,h.event_type::text AS event_type,h.status::text AS status,
               h.format::text AS format,h.start_at,h.end_at,h.registration_open_at,h.registration_close_at,
               l.city,l.state_region,l.country_code,h.is_free,h.quality_score,h.source_count,h.last_verified_at,
               (SELECT MAX(pp.normalized_usd_amount) FROM prize_pools pp WHERE pp.hackathon_id=h.id) AS prize_usd
        FROM recommendation_scores r
        JOIN hackathons h ON h.id=r.hackathon_id AND h.is_active=true
        LEFT JOIN locations l ON l.id=h.primary_location_id
        WHERE r.user_id=:user_id AND r.eligibility_result::text <> 'INELIGIBLE'
          AND r.generated_at=(SELECT MAX(r2.generated_at) FROM recommendation_scores r2 WHERE r2.user_id=r.user_id AND r2.hackathon_id=r.hackathon_id)
        ORDER BY r.score DESC NULLS LAST,h.start_at NULLS LAST,h.id
        LIMIT :limit
    """),{"user_id":session.user.id,"limit":limit}).mappings().all()
    items=[]
    from hackathon_api.schemas import EligibilityView, HackathonListItem, RecommendationItem
    for raw in rows:
        row=dict(raw)
        event={k:row[k] for k in ("id","slug","name","short_description","event_type","status","format","start_at","end_at","registration_open_at","registration_close_at","city","state_region","country_code","prize_usd","is_free","quality_score","source_count","last_verified_at")}
        items.append(RecommendationItem(
            hackathon=HackathonListItem.model_validate(event),
            eligibility=EligibilityView(result=row["eligibility_result"],rules_version="w5-eligibility-v1"),
            score=float(row["score"]) if row["score"] is not None else None,
            positive_reasons=list(row["positive_reasons"] or []),negative_reasons=list(row["negative_reasons"] or []),
            component_scores={k:float(v) for k,v in (row["components"] or {}).items()},model_version=row["model_version"],input_hash=row["input_hash"] or "legacy",preference_version=row["profile_version"] or 0,
        ))
    return RecommendationPage(items=items,generated_at=datetime.now(UTC))


@router.get("/saved-searches", response_model=list[SavedSearchView])
def list_saved_searches(session: Annotated[SessionContext, Depends(current_session)], connection: Annotated[Connection, Depends(connection_dependency)]):
    return PersonalizationRepository(connection).saved_searches(session.user.id)


@router.post("/saved-searches", response_model=SavedSearchView, status_code=201)
def create_saved_search(payload: SavedSearchCreate, session: Annotated[SessionContext, Depends(require_csrf)], connection: Annotated[Connection, Depends(connection_dependency)]):
    return PersonalizationRepository(connection).create_saved_search(session.user.id,payload)


@router.patch("/saved-searches/{search_id}", response_model=SavedSearchView)
def update_saved_search(search_id: UUID, payload: SavedSearchUpdate, session: Annotated[SessionContext, Depends(require_csrf)], connection: Annotated[Connection, Depends(connection_dependency)]):
    result=PersonalizationRepository(connection).update_saved_search(session.user.id,search_id,payload)
    if not result: raise HTTPException(404,"saved search not found")
    return result


@router.delete("/saved-searches/{search_id}", status_code=204)
def delete_saved_search(search_id: UUID, session: Annotated[SessionContext, Depends(require_csrf)], connection: Annotated[Connection, Depends(connection_dependency)]):
    PersonalizationRepository(connection).delete_saved_search(session.user.id,search_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/alerts", response_model=list[AlertEventView])
def alerts(session: Annotated[SessionContext, Depends(current_session)], connection: Annotated[Connection, Depends(connection_dependency)], limit:int=Query(50,ge=1,le=100)):
    return PersonalizationRepository(connection).alerts(session.user.id,limit)


@router.patch("/alerts/{alert_id}/read", status_code=204)
def mark_alert_read(alert_id:UUID,session:Annotated[SessionContext,Depends(require_csrf)],connection:Annotated[Connection,Depends(connection_dependency)]):
    if not PersonalizationRepository(connection).mark_alert_read(session.user.id,alert_id): raise HTTPException(404,"alert not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/alert-preferences", response_model=AlertPreferenceView)
def alert_preferences(
    session: Annotated[SessionContext, Depends(current_session)],
    connection: Annotated[Connection, Depends(connection_dependency)],
):
    return PersonalizationRepository(connection).new_match_notification_preferences(session.user.id)


@router.put("/alert-preferences", status_code=204)
def alert_preference(payload:AlertPreferenceUpdate,session:Annotated[SessionContext,Depends(require_csrf)],connection:Annotated[Connection,Depends(connection_dependency)]):
    PersonalizationRepository(connection).set_alert_preference(session.user.id,payload.channel,payload.alert_type,payload.enabled)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
