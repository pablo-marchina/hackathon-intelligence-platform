from pathlib import Path
from hackathon_api.repositories.hackathons import build_hackathon_query, encode_cursor, decode_cursor
from hackathon_api.schemas import HackathonFilters


def test_p0_filter_query_contains_every_acceptance_family():
    f=HackathonFilters(q='ai',status=['UPCOMING'],event_type=['hackathon'],registration_state=['OPEN'],format=['ONLINE'],country_code=['BR'],city='São Paulo',free_only=True,min_prize_usd=10000,max_prize_usd=50000,track=['ai'],technology=['python'],organizer=['openai'],sponsor=['nvidia'],team_size=4,student_required=True,individual_allowed=False,eligible_country='BR')
    spec=build_hackathon_query(f)
    sql=spec.sql
    for token in ('registration_close_at','country_code','prize_pools','hackathon_tracks','hackathon_technologies','hackathon_organizations','SPONSOR','eligibility_rules','eligibility_countries'):
        assert token in sql
    assert spec.params['eligible_country']=='BR' and spec.params['team_size']==4


def test_keyset_cursor_is_bound_to_sort_and_filter_fingerprint():
    f=HackathonFilters(q='ai',sort='relevance')
    cursor=encode_cursor({'id':'00000000-0000-0000-0000-000000000001','rank_score':.75,'quality_sort':90,'start_sort':'2026-10-01T00:00:00+00:00','deadline_sort':'2026-09-30T00:00:00+00:00'},f)
    payload=decode_cursor(cursor)
    assert payload['sort']=='relevance' and payload['f']
    f2=f.model_copy(update={'cursor':cursor})
    assert ':cursor_id' in build_hackathon_query(f2).sql


def test_account_linking_and_privacy_boundaries_are_explicit():
    root=Path(__file__).resolve().parents[3]
    repo=(root/'apps/api/src/hackathon_api/repositories/auth.py').read_text()
    migration=(root/'db/sql/0004_w4_api_search_frontend.sql').read_text()
    assert 'provider_subject' in repo and 'verified_email' in repo
    assert 'ON CONFLICT(email)' in repo and 'ON CONFLICT(provider,provider_subject)' in repo
    assert 'privacy_requests' in migration and "'EXPORT','DELETE'" in migration


def test_detail_page_renders_rich_canonical_sections():
    root=Path(__file__).resolve().parents[3]
    detail=(root/'apps/web/app/hackathons/[slug]/page.tsx').read_text()
    for text in ('Eligibility & teams','Prizes','Timeline & phases','Organizations & sponsors','Resources','Judging','Verified links','Freshness, changes & evidence','Registration / application','Canonical official event page'):
        assert text in detail
    assert 'application/ld+json' in detail
