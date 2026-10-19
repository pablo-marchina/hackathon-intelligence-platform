from pathlib import Path

ROOT=Path(__file__).parents[3]

def test_w5_frontend_routes_exist_and_use_v1_api() -> None:
    routes=["preferences","recommendations","saved-searches","alerts"]
    for route in routes:
        text=(ROOT/f"apps/web/app/{route}/page.tsx").read_text()
        assert "/v1/me/" in text
    assert (ROOT/"apps/web/components/PreferenceEditor.tsx").exists()
    assert (ROOT/"apps/web/components/SavedSearchManager.tsx").exists()
