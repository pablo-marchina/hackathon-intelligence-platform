"""Coverage contract for the user's eleven named hackathon listing sources.

Registry membership is NOT equivalent to collection authorization or crawl success.
"""
from pathlib import Path
from urllib.parse import urlsplit

from hackathon_sources.models import PermissionStatus
from hackathon_sources.registry import load_source_definitions

ROOT = Path(__file__).resolve().parents[3]
SEEDS = ROOT / "db/seeds/sources_v0_1.json"

EXPECTED = {
    "hackerearth": "https://www.hackerearth.com/challenges/",
    "hackathonradar": "https://www.hackathonradar.com/database?page=1&limit=50",
    "devpost": "https://devpost.com/hackathons?order_by=deadline",
    "taikai": "https://taikai.network/br/hackathons?sort_by=date&page=1",
    "devfolio": "https://devfolio.co/search?primary_filter=hackathons&type=application_open",
    "wemakedevs": "https://www.wemakedevs.org/hackathons",
    "mlh": "https://www.mlh.com/seasons/2026/events",
    "dorahacks": "https://dorahacks.io/hackathon?status=ongoing",
    "hackatrack": "https://www.hackatrack.net/",
    "hackathonsnear": "https://hackathonsnear.me/",
    "lablab_ai": "https://lablab.ai/ai-hackathons",
}


def test_every_requested_source_and_listing_url_is_registered() -> None:
    definitions = {d.key: d for d in load_source_definitions(SEEDS)}
    for key, url in EXPECTED.items():
        assert key in definitions, key
        assert url in {str(item) for item in definitions[key].seed_urls}
        domain = (urlsplit(url).hostname or "").lower()
        allowed = {definitions[key].domain, *definitions[key].allowed_hosts}
        assert domain in allowed, (key, domain)


def test_new_directory_sources_remain_unapproved_by_default() -> None:
    definitions = {d.key: d for d in load_source_definitions(SEEDS)}
    for key in ("hackathonradar", "wemakedevs", "hackatrack", "hackathonsnear", "lablab_ai"):
        source = definitions[key]
        assert source.permission_status is PermissionStatus.REVIEW_REQUIRED
        assert not source.detail_extraction_enabled


def test_restricted_devpost_remains_restricted() -> None:
    definition = next(d for d in load_source_definitions(SEEDS) if d.key == "devpost")
    assert definition.permission_status is PermissionStatus.RESTRICTED
    assert not definition.detail_extraction_enabled
