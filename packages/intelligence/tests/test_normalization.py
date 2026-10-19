from decimal import Decimal

from hackathon_intelligence.normalization import normalize_money, normalize_name, parse_datetime
from hackathon_intelligence.taxonomy import normalize_event_type, normalize_track


def test_name_and_taxonomy_normalization() -> None:
    assert normalize_name("São Paulo AI-Hackathon!") == "sao paulo ai hackathon"
    assert normalize_event_type("Game Jam") == "game_jam"
    assert normalize_track("GenAI") == "Generative AI"


def test_datetime_gets_timezone() -> None:
    assert parse_datetime("2026-10-01 10:00").tzinfo is not None


def test_money_preserves_original_and_normalizes() -> None:
    value = normalize_money("1000", "BRL", usd_fx_rate="0.19")
    assert value.original_amount == Decimal("1000")
    assert value.normalized_usd_amount == Decimal("190.00")
