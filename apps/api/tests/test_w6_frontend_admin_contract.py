"""No browser-embedded service credentials and fully wired W6 UI actions."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DASHBOARD = ROOT / 'apps/web/components/AdminDashboard.tsx'


def test_admin_dashboard_uses_session_and_csrf_and_has_mutations():
    page = DASHBOARD.read_text(encoding='utf-8')
    assert "credentials:'include'" in page
    assert "'/me'" in page
    assert "'X-CSRF-Token':options.csrf" in page
    assert "'/source-health'" in page
    assert "'/review-queue?limit=100'" in page
    assert "'/quality?limit=100'" in page
    assert "'/audit?limit=50'" in page
    assert "'/search/reindex'" in page
    assert "{active:!source.active}" in page
    assert 'role=\"alert\"' in page
    assert "'ADMIN_API_KEY'" not in page
    assert 'NEXT_PUBLIC_ADMIN' not in page
    assert "role==='ADMIN'" in page


def test_admin_dashboard_in_navigation():
    layout = (ROOT / 'apps/web/app/layout.tsx').read_text(encoding='utf-8')
    assert '<Link href="/admin">Operations</Link>' in layout
