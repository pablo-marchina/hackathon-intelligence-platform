from datetime import UTC, datetime

from hackathon_intelligence.jsonld import extract_event_jsonld


def test_extract_event_jsonld() -> None:
    html = '''<html><script type="application/ld+json">{
      "@context":"https://schema.org","@type":"Event","name":"AI Hackathon 2026",
      "startDate":"2026-10-01T10:00:00-03:00","endDate":"2026-10-02T18:00:00-03:00",
      "url":"https://example.org/hack","organizer":{"@type":"Organization","name":"Example Org"},
      "location":{"@type":"Place","name":"São Paulo"}
    }</script></html>'''
    events = extract_event_jsonld(html, observed_at=datetime.now(UTC))
    assert len(events) == 1
    assert events[0].name == "AI Hackathon 2026"
    assert events[0].organizer_name == "Example Org"
    assert events[0].start_at is not None
    assert any(item.field_path == "start_at" for item in events[0].evidence)
