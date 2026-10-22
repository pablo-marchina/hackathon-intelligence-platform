"""The page must expose backend-supported filtering, not just the seven W4 inputs."""

import re
from pathlib import Path

from hackathon_api.schemas import HackathonFilters

ROOT = Path(__file__).resolve().parents[3]


def test_search_page_exposes_every_user_filter():
    text = (ROOT / "apps/web/app/search/page.tsx").read_text()
    controls = set(re.findall(r'name="([a-z_]+)"', text))
    query_fields = set(HackathonFilters.model_fields) - {"cursor", "limit"}
    assert query_fields <= controls, sorted(query_fields - controls)
    assert 'type="checkbox"' in text
    assert 'type="date"' in text
    assert 'method="get"' in text


def test_search_links_keep_cursor_and_all_filters():
    text = (ROOT / "apps/web/app/search/page.tsx").read_text()
    assert "query.set('cursor', first(params.cursor))" in text
    assert "next.set('cursor', body.next_cursor)" in text
    assert "new URLSearchParams(query)" in text
