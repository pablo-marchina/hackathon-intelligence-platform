from pathlib import Path


def test_frontend_has_real_api_driven_search_detail_series_org_and_library():
    root = Path(__file__).resolve().parents[3] / "apps/web"
    search = (root / "app/search/page.tsx").read_text()
    detail = (root / "app/hackathons/[slug]/page.tsx").read_text()
    lib = (root / "app/library/page.tsx").read_text()
    assert "searchHackathons" in search and "URLSearchParams" in search
    assert "mock" not in search.lower()
    assert "application/ld+json" in detail and "recent_changes" in detail and "evidence" in detail
    assert "/v1/me/library" in lib
    assert (root / "app/organizations/[slug]/page.tsx").exists()
    assert (root / "app/series/[slug]/page.tsx").exists()


def test_frontend_distinguishes_empty_results_and_outage_and_is_responsive():
    root = Path(__file__).resolve().parents[3] / "apps/web"
    search = (root / "app/search/page.tsx").read_text()
    css = (root / "app/globals.css").read_text()
    assert "Data-source outage" in search
    assert "No matching hackathons" in search
    assert "@media(max-width:850px)" in css
