from __future__ import annotations
from typing import Annotated,Any
from fastapi import APIRouter,Depends,HTTPException,Query,Response,status
from sqlalchemy import Connection
from hackathon_api.db import connection_dependency
from hackathon_api.repositories.auth import AuthRepository
from hackathon_api.repositories.users import UserRepository
from hackathon_api.schemas import UserHackathonStateUpdate,UserLibraryPage,UserProfileUpdate
from hackathon_api.security import SessionContext,current_session,require_csrf
router=APIRouter(prefix='/me',tags=['users'])
@router.get('')
def me(session:Annotated[SessionContext,Depends(current_session)],connection:Annotated[Connection,Depends(connection_dependency)])->dict[str,Any]:return UserRepository(connection).profile(session.user.id)
@router.patch('')
def update_me(payload:UserProfileUpdate,session:Annotated[SessionContext,Depends(require_csrf)],connection:Annotated[Connection,Depends(connection_dependency)]):return UserRepository(connection).update_profile(session.user.id,payload)
@router.get('/library',response_model=UserLibraryPage)
def library(session:Annotated[SessionContext,Depends(current_session)],connection:Annotated[Connection,Depends(connection_dependency)],state:Annotated[list[str]|None,Query()]=None,limit:int=Query(50,ge=1,le=100),cursor:str|None=None):return UserRepository(connection).library(session.user.id,state or [],limit,cursor)
@router.put('/hackathons/{slug}/state')
def set_state(slug:str,payload:UserHackathonStateUpdate,session:Annotated[SessionContext,Depends(require_csrf)],connection:Annotated[Connection,Depends(connection_dependency)]):
 r=UserRepository(connection).set_event_state(session.user.id,slug,payload.state)
 if not r:raise HTTPException(404,'hackathon not found')
 return r
@router.delete('/hackathons/{slug}/state',status_code=204)
def delete_state(slug:str,session:Annotated[SessionContext,Depends(require_csrf)],connection:Annotated[Connection,Depends(connection_dependency)]):UserRepository(connection).delete_event_state(session.user.id,slug);return Response(status_code=status.HTTP_204_NO_CONTENT)
@router.post('/export')
def export_user(session:Annotated[SessionContext,Depends(require_csrf)],connection:Annotated[Connection,Depends(connection_dependency)]):return AuthRepository(connection).export_user(session.user.id)
