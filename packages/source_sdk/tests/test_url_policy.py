from hackathon_sources.models import IngestionMode, PermissionStatus, SourceDefinition
from hackathon_sources.policy import may_fetch_details
from hackathon_sources.url import canonicalize_url


def source(**overrides):
    data = {
        "key": "example",
        "name": "Example",
        "domain": "example.com",
        "source_family": "example",
        "source_type": "platform",
        "ingestion_mode": IngestionMode.STATIC_HTML,
        "permission_status": PermissionStatus.ALLOWED,
        "detail_extraction_enabled": True,
    }
    data.update(overrides)
    return SourceDefinition(**data)


def test_canonicalize_url_removes_tracking_and_fragment() -> None:
    assert canonicalize_url("HTTPS://Example.com/events/?utm_source=x&b=2&a=1#top") == "https://example.com/events?a=1&b=2"


def test_discovery_only_source_cannot_be_fetched() -> None:
    decision = may_fetch_details(source(ingestion_mode=IngestionMode.DISCOVERY_ONLY))
    assert not decision.allowed


def test_review_required_source_cannot_be_fetched() -> None:
    decision = may_fetch_details(source(permission_status=PermissionStatus.REVIEW_REQUIRED))
    assert not decision.allowed


def test_allowed_source_can_be_fetched() -> None:
    assert may_fetch_details(source()).allowed


def test_api_source_can_be_allowed_without_html_robots_semantics() -> None:
    api_source = source(ingestion_mode=IngestionMode.API, domain="api.example.com")
    assert may_fetch_details(api_source).allowed
