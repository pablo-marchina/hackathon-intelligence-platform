from hackathon_api.repositories.hackathons import (
    build_hackathon_query,
    decode_cursor,
    encode_cursor,
)
from hackathon_api.schemas import HackathonFilters


def test_filter_query_only_includes_requested_filters() -> None:
    spec = build_hackathon_query(
        HackathonFilters(
            q="AI agents",
            country_code="br",
            eligible_country="br",
            free_only=True,
            min_prize_usd=10000,
            track=["fintech"],
        )
    )
    assert "websearch_to_tsquery" in spec.sql
    assert "l.country_code=ANY" in spec.sql
    assert "eligibility_countries" in spec.sql
    assert "h.is_free=true" in spec.sql
    assert "prize_pools" in spec.sql
    assert "hackathon_tracks" in spec.sql
    assert spec.params["country_code"] == "BR"
    assert spec.params["eligible_country"] == "BR"
    assert spec.params["min_prize"] == 10000


def test_cursor_roundtrip() -> None:
    token = encode_cursor(
        {"id": "00000000-0000-0000-0000-000000000001", "rank_score": 0.4, "start_at": None}
    )
    assert decode_cursor(token)["id"].endswith("0001")
