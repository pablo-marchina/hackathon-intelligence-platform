from __future__ import annotations
import secrets
from typing import Annotated
from fastapi import APIRouter,Depends,HTTPException,Query,Request,Response,status
from fastapi.responses import RedirectResponse
from sqlalchemy import Connection
from hackathon_api.auth import PROVIDERS,authorization_url,create_pkce,exchange_profile,hash_secret,keyed_hash,provider_credentials,sign_oauth_state,verify_oauth_state
from hackathon_api.db import connection_dependency
from hackathon_api.repositories.auth import AuthRepository
from hackathon_api.schemas import AuthUser
from hackathon_api.security import SessionContext,current_session,require_csrf
from hackathon_shared.settings import get_settings
router=APIRouter(prefix='/auth',tags=['auth'])
def _secure():return get_settings().app_env=='production'
@router.get('/{provider}/login')
def login(provider:str,return_to:str=Query('/')):
 provider=provider.lower();s=get_settings()
 if provider not in PROVIDERS:raise HTTPException(404,'unsupported provider')
 if not s.auth_state_secret:raise HTTPException(503,'authentication is not configured')
 provider_credentials(s,provider)
 if not return_to.startswith('/') or return_to.startswith('//'):raise HTTPException(400,'invalid return_to')
 state=sign_oauth_state(provider=provider,return_to=return_to,secret=s.auth_state_secret);ver,ch=create_pkce();r=RedirectResponse(authorization_url(s,provider,state,ch),302);r.set_cookie('hip_oauth_state',state,httponly=True,secure=_secure(),samesite='lax',max_age=600,path='/v1/auth');r.set_cookie('hip_oauth_pkce',ver,httponly=True,secure=_secure(),samesite='lax',max_age=600,path='/v1/auth');return r
@router.get('/{provider}/callback')
async def callback(provider:str,request:Request,code:str,state:str,connection:Annotated[Connection,Depends(connection_dependency)]):
 s=get_settings();sc=request.cookies.get('hip_oauth_state');ver=request.cookies.get('hip_oauth_pkce')
 if not s.auth_state_secret or not sc or not ver or not secrets.compare_digest(sc,state):raise HTTPException(400,'oauth state mismatch')
 parsed=verify_oauth_state(state,s.auth_state_secret);prof=await exchange_profile(s,provider.lower(),code,ver);u=AuthRepository(connection).upsert_identity(provider=prof.provider,provider_subject=prof.subject,verified_email=prof.email if prof.email_verified else None,display_name=prof.display_name);tok=secrets.token_urlsafe(48);csrf=secrets.token_urlsafe(32);AuthRepository(connection).create_session(user_id=u.id,provider=prof.provider,token_hash=hash_secret(tok),csrf_hash=hash_secret(csrf),ttl_seconds=s.session_ttl_seconds,user_agent_hash=keyed_hash(request.headers.get('user-agent'),s.auth_state_secret),ip_hash=keyed_hash(request.client.host if request.client else None,s.auth_state_secret));r=RedirectResponse(f"{s.web_base_url.rstrip('/')}{parsed['return_to']}",303);r.set_cookie(s.session_cookie_name,tok,httponly=True,secure=_secure(),samesite='lax',max_age=s.session_ttl_seconds,path='/');r.set_cookie(s.csrf_cookie_name,csrf,httponly=False,secure=_secure(),samesite='lax',max_age=s.session_ttl_seconds,path='/');return r
@router.get('/session',response_model=AuthUser)
def session_user(session:Annotated[SessionContext,Depends(current_session)]):return session.user
@router.post('/logout',status_code=204)
def logout(response:Response,session:Annotated[SessionContext,Depends(require_csrf)],connection:Annotated[Connection,Depends(connection_dependency)]):AuthRepository(connection).revoke(session.token_hash);response.status_code=status.HTTP_204_NO_CONTENT;return response
