from __future__ import annotations
import base64,hashlib,hmac,json,secrets
from dataclasses import dataclass
from urllib.parse import urlencode
import httpx
from hackathon_shared.settings import Settings
@dataclass(frozen=True)
class OAuthProfile: provider:str; subject:str; email:str|None; email_verified:bool; display_name:str|None
@dataclass(frozen=True)
class OAuthProvider: name:str; authorize_url:str; token_url:str; scopes:str
PROVIDERS={'google':OAuthProvider('GOOGLE','https://accounts.google.com/o/oauth2/v2/auth','https://oauth2.googleapis.com/token','openid email profile'),'github':OAuthProvider('GITHUB','https://github.com/login/oauth/authorize','https://github.com/login/oauth/access_token','read:user user:email')}
def hash_secret(v:str)->str:return hashlib.sha256(v.encode()).hexdigest()
def keyed_hash(v:str|None,s:str)->str|None:return hmac.new(s.encode(),v.encode(),hashlib.sha256).hexdigest() if v else None
def _b64(b:bytes)->str:return base64.urlsafe_b64encode(b).decode().rstrip('=')
def create_pkce()->tuple[str,str]:
 v=_b64(secrets.token_bytes(48));return v,_b64(hashlib.sha256(v.encode()).digest())
def sign_oauth_state(*,provider:str,return_to:str,secret:str)->str:
 p=_b64(json.dumps({'p':provider,'r':return_to,'n':secrets.token_urlsafe(24)},separators=(',',':')).encode());return f"{p}.{_b64(hmac.new(secret.encode(),p.encode(),hashlib.sha256).digest())}"
def verify_oauth_state(state:str,secret:str)->dict[str,str]:
 try:
  p,sig=state.split('.',1);exp=_b64(hmac.new(secret.encode(),p.encode(),hashlib.sha256).digest())
  if not hmac.compare_digest(sig,exp):raise ValueError
  d=json.loads(base64.urlsafe_b64decode(p+'='*(-len(p)%4)).decode());r=str(d['r'])
  if not r.startswith('/') or r.startswith('//'):raise ValueError
  return {'provider':str(d['p']),'return_to':r}
 except Exception as exc:raise ValueError('invalid oauth state') from exc
def provider_credentials(settings:Settings,provider:str)->tuple[str,str]:
 cid,sec=(settings.google_oauth_client_id,settings.google_oauth_client_secret) if provider=='google' else (settings.github_oauth_client_id,settings.github_oauth_client_secret) if provider=='github' else (None,None)
 if not cid or not sec:raise RuntimeError(f'{provider} oauth is not configured')
 return cid,sec
def authorization_url(settings:Settings,provider:str,state:str,challenge:str)->str:
 c=PROVIDERS[provider];cid,_=provider_credentials(settings,provider);redirect=f"{settings.public_api_base_url.rstrip('/')}/v1/auth/{provider}/callback";p={'client_id':cid,'redirect_uri':redirect,'scope':c.scopes,'state':state,'code_challenge':challenge,'code_challenge_method':'S256'}
 if provider=='google':p.update(response_type='code',access_type='online')
 return f"{c.authorize_url}?{urlencode(p)}"
async def exchange_profile(settings:Settings,provider:str,code:str,verifier:str)->OAuthProfile:
 c=PROVIDERS[provider];cid,sec=provider_credentials(settings,provider);redirect=f"{settings.public_api_base_url.rstrip('/')}/v1/auth/{provider}/callback"
 async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
  tr=await client.post(c.token_url,data={'client_id':cid,'client_secret':sec,'code':code,'redirect_uri':redirect,'grant_type':'authorization_code','code_verifier':verifier},headers={'Accept':'application/json'});tr.raise_for_status();token=tr.json().get('access_token')
  if not token:raise RuntimeError('provider did not return access token')
  h={'Authorization':f'Bearer {token}','Accept':'application/json'}
  if provider=='google':
   r=await client.get('https://openidconnect.googleapis.com/v1/userinfo',headers=h);r.raise_for_status();d=r.json();return OAuthProfile('GOOGLE',str(d['sub']),d.get('email'),bool(d.get('email_verified')),d.get('name'))
  r=await client.get('https://api.github.com/user',headers={**h,'X-GitHub-Api-Version':'2022-11-28'});r.raise_for_status();d=r.json();email=d.get('email');verified=bool(email)
  return OAuthProfile('GITHUB',str(d['id']),email,verified,d.get('name') or d.get('login'))
