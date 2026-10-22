"""New-source discovery is a review queue, not a crawl authorization."""

from scripts.ops.propose_hackathon_sources import propose, safe_candidate_url


def test_candidate_validation_rejects_unsafe_urls():
    for candidate in (
        "http://hackathons.org",
        "https://127.0.0.1/a",
        "https://localhost/a",
        "https://events.local/a",
        "https://username:pass@events.org/a",
        "https://events.org:8080/a",
        "javascript:alert(1)",
        "https://[::1]/a",
    ):
        assert safe_candidate_url(candidate) is None, candidate
    assert safe_candidate_url("https://new-site.org/events?x=2#anchor") == (
        "https://new-site.org/events?x=2",
        "new-site.org",
    )


def test_new_sites_are_review_only_and_grouped_by_domain():
    report = propose(
        [
            "https://new-hacks.org/hackathons",
            "https://www.new-hacks.org/events",
            "https://known.org/hackathons",
            "https://127.0.0.1/private",
        ],
        [_source()],
    )
    assert report["status"] == "REVIEW_ONLY"
    assert report["new_source_count"] == 1
    assert report["skipped_registered_urls"] == 1
    assert report["rejected_or_over_budget_urls"] == 1
    src = report["sources"][0]
    assert src["permission_status"] == "REVIEW_REQUIRED"
    assert src["discovery_enabled"] is False
    assert src["detail_extraction_enabled"] is False
    assert len(src["seed_urls"]) == 2


def _source():
    from hackathon_sources.models import SourceDefinition

    return SourceDefinition(
        key="known",
        name="Known",
        domain="known.org",
        source_family="known",
        source_type="hackathon_directory",
        ingestion_mode="STATIC_HTML",
        permission_status="REVIEW_REQUIRED",
    )
