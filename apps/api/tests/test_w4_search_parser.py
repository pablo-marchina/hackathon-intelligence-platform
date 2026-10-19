from hackathon_api.search_parser import parse_natural_language_query

def test_natural_language_parser_compiles_to_structured_schema():
 r=parse_natural_language_query('free online hackathons in Brazil registration open team of 4 prize over $10k AI agents');assert r.filters.free_only is True;assert r.filters.format==['ONLINE'];assert r.filters.event_type==['hackathon'];assert r.filters.country_code==['BR'];assert r.filters.registration_state==['OPEN'];assert r.filters.team_size==4;assert r.filters.min_prize_usd==10000;assert r.filters.q=='AI agents'
def test_unsupported_constraint_is_surfaced_not_hallucinated():
 r=parse_natural_language_query('best easy win hackathon online');assert 'best' in [x.lower() for x in r.unsupported_constraints];assert any('easy win' in x.lower() for x in r.unsupported_constraints);assert r.filters.format==['ONLINE']
