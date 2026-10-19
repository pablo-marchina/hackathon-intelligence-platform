import os
os.environ.setdefault('DATABASE_URL','postgresql://postgres:postgres@localhost:5432/hackathon_intelligence')
from hackathon_api.main import app

def test_w4_openapi_contract_exposes_versioned_public_surface():
 p=set(app.openapi()['paths']);expected={'/v1/hackathons','/v1/hackathons/{slug}','/v1/organizations','/v1/organizations/{slug}','/v1/series','/v1/series/{slug}','/v1/tracks','/v1/technologies','/v1/sources','/v1/search/parse','/v1/auth/{provider}/login','/v1/auth/{provider}/callback','/v1/auth/session','/v1/me','/v1/me/library','/v1/me/hackathons/{slug}/state'};assert expected<=p;assert app.version=='1.0.0'
